from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

import os
import tempfile

from apps.stock.models import Stock
from apps.market.models import Candle, FeatureSnapshot
from apps.system.models import ModelArtifact, TrainingDataset, TrainingDatasetItem
from core.ml.dataset import DatasetConfig, build_dataset_from_candles
from core.ml.filter import MLFilterEngine
from core.ml.predictor import LightGBMPredictor
from core.ml.training import load_predictor_from_artifact, train_from_dataset
from core.ml.validation import (
    aggregate_fold_metrics,
    generate_walk_forward_folds,
    run_walk_forward,
)


class WalkForwardTestCase(SimpleTestCase):
    """Walk-Forward 분할/러너 검증 (DB 불필요)."""

    def test_fold_boundaries_and_embargo(self):
        folds = generate_walk_forward_folds(
            n_samples=100, train_size=40, test_size=20, embargo=5
        )
        self.assertEqual(len(folds), 2)
        for f in folds:
            # embargo gap 준수: train 은 test 시작 5봉 전에 종료
            self.assertEqual(f.test_start - f.train_end, 5)
            # train 과 test 는 겹치지 않는다
            self.assertLessEqual(f.train_end, f.test_start)
        # test 블록은 연속 비겹침
        self.assertEqual(folds[0].test_end, folds[1].test_start)

    def test_rolling_vs_anchored(self):
        rolling = generate_walk_forward_folds(100, 40, 20, embargo=5, anchored=False)
        anchored = generate_walk_forward_folds(100, 40, 20, embargo=5, anchored=True)
        # 롤링: 두번째 폴드 train_start 가 앞으로 이동
        self.assertGreater(rolling[1].train_start, 0)
        # 앵커드: 항상 0
        self.assertTrue(all(f.train_start == 0 for f in anchored))

    def test_run_walk_forward_with_dummy_model(self):
        X = list(range(100))
        y = list(range(100))
        folds = generate_walk_forward_folds(100, 40, 20, embargo=5)

        def train_fn(X_tr, y_tr):
            return sum(y_tr) / len(y_tr)  # 평균 예측 모델

        def predict_fn(model, X_te):
            return [model] * len(X_te)

        def metric_fn(y_te, preds):
            mae = sum(abs(a - b) for a, b in zip(y_te, preds)) / len(y_te)
            return {"mae": mae}

        results = run_walk_forward(X, y, folds, train_fn, predict_fn, metric_fn)
        self.assertEqual(len(results), len(folds))
        for r in results:
            self.assertEqual(r.fold.n_test, 20)
            self.assertIn("mae", r.metrics)

        agg = aggregate_fold_metrics(results)
        self.assertIn("mae_mean", agg)
        self.assertIn("mae_std", agg)
        self.assertEqual(agg["n_folds"], len(folds))

    def test_invalid_params_raise(self):
        with self.assertRaises(ValueError):
            generate_walk_forward_folds(100, 0, 20)
        with self.assertRaises(ValueError):
            generate_walk_forward_folds(100, 40, 20, embargo=-1)


class DatasetBuilderTestCase(TestCase):
    def setUp(self):
        self.stock = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="005930", name="삼성전자"
        )
        base = datetime(2024, 1, 2, 9, 0, tzinfo=dt_timezone.utc)
        # 완만한 상승 추세 (익절 배리어가 자주 도달하도록)
        for i in range(40):
            price = Decimal("70000") + Decimal(i) * Decimal("50")
            Candle.objects.create(
                stock=self.stock,
                timeframe=Candle.Timeframe.MIN_1,
                opened_at=base + timedelta(minutes=i),
                open_price=price,
                high_price=price,
                low_price=price,
                close_price=price,
                volume=Decimal("1000"),
                source="test",
            )

    def test_build_dataset(self):
        dataset = build_dataset_from_candles(
            self.stock,
            name="samsung_1m",
            version="v1",
            # cost=0 으로 배리어 라벨 로직 자체를 검증(비용 효과는 라벨러 테스트에서 별도 검증)
            config=DatasetConfig(
                upper=0.001, lower=0.005, horizon=10, min_history=20, cost=0.0
            ),
        )

        self.assertEqual(dataset.status, TrainingDataset.Status.READY)
        self.assertIsNotNone(dataset.completed_at)
        self.assertEqual(dataset.label_definition["method"], "triple_barrier")

        items = TrainingDatasetItem.objects.filter(training_dataset=dataset)
        self.assertGreater(items.count(), 0)

        # 라벨은 0/1만, feature_payload/스냅샷 존재
        for item in items:
            self.assertIn(item.label, (0, 1))
            self.assertIsNotNone(item.feature_snapshot_id)
            self.assertIn("close", item.feature_payload)

        # Feature 스냅샷이 샘플 수만큼 생성됨
        self.assertEqual(
            FeatureSnapshot.objects.filter(stock=self.stock).count(), items.count()
        )

        # 상승 추세 + 낮은 익절 배리어 -> 최소 일부는 수익 라벨(1)
        self.assertGreater(items.filter(label=1).count(), 0)

    def test_empty_candles_raises(self):
        empty_stock = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="000000", name="빈종목"
        )
        with self.assertRaises(ValueError):
            build_dataset_from_candles(empty_stock, name="x", version="v1")


class LightGBMPredictorTestCase(SimpleTestCase):
    """예측기 학습/추론/직렬화 검증 (DB 불필요)."""

    def _data(self):
        feats, labels = [], []
        for i in range(60):
            if i % 2 == 0:
                feats.append({"x": 1.0, "y": 0.5})
                labels.append(1)
            else:
                feats.append({"x": -1.0, "y": 0.5})
                labels.append(0)
        return feats, labels

    def test_train_predict_metrics(self):
        feats, labels = self._data()
        pred, metrics = LightGBMPredictor.train(feats, labels, num_boost_round=30)
        self.assertIn("train_auc", metrics)
        self.assertEqual(metrics["n_features"], 2)
        # x가 클래스를 분리하므로 high-x 확률 > low-x 확률
        self.assertGreater(
            pred.predict_proba({"x": 1.0, "y": 0.5}),
            pred.predict_proba({"x": -1.0, "y": 0.5}),
        )

    def test_save_load_roundtrip(self):
        feats, labels = self._data()
        pred, _ = LightGBMPredictor.train(feats, labels, num_boost_round=30)
        p_before = pred.predict_proba({"x": 1.0, "y": 0.5})
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.pkl")
            checksum = pred.save(path)
            self.assertTrue(os.path.exists(path))
            self.assertEqual(len(checksum), 64)  # sha256 hex
            loaded = LightGBMPredictor.load(path)
            self.assertAlmostEqual(
                loaded.predict_proba({"x": 1.0, "y": 0.5}), p_before, places=6
            )

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            LightGBMPredictor.train([], [])


class TrainingPipelineTestCase(TestCase):
    """데이터셋 -> 학습 -> ModelArtifact -> MLFilterEngine 연동 검증."""

    def setUp(self):
        self.stock = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="005930", name="삼성전자"
        )
        base = datetime(2024, 1, 2, 9, 0, tzinfo=dt_timezone.utc)
        # 삼각파(상승5/하락5) -> 익절/손절 배리어가 모두 발생하여 라벨이 섞이도록
        price = 70000
        for i in range(60):
            step = 200 if (i % 10) < 5 else -200
            price += step
            p = Decimal(str(price))
            Candle.objects.create(
                stock=self.stock,
                timeframe=Candle.Timeframe.MIN_1,
                opened_at=base + timedelta(minutes=i),
                open_price=p,
                high_price=p,
                low_price=p,
                close_price=p,
                volume=Decimal("1000"),
                source="test",
            )

    def test_train_deploy_and_infer(self):
        dataset = build_dataset_from_candles(
            self.stock,
            name="samsung_1m",
            version="v1",
            config=DatasetConfig(
                upper=0.002, lower=0.002, horizon=5, min_history=20, cost=0.0
            ),
        )
        self.assertGreater(dataset.items.count(), 0)

        with tempfile.TemporaryDirectory() as d:
            artifact = train_from_dataset(
                dataset,
                model_name="lgb_signal_filter",
                version="v1",
                artifact_dir=d,
                deploy=True,
            )
            self.assertEqual(artifact.status, ModelArtifact.Status.DEPLOYED)
            self.assertTrue(os.path.exists(artifact.artifact_uri))
            self.assertIn("train_auc", artifact.metrics_payload)
            self.assertEqual(artifact.training_dataset_id, dataset.id)

            # 로드 후 추론
            predictor = load_predictor_from_artifact(artifact)
            prob = predictor.predict_proba(dataset.items.first().feature_payload)
            self.assertTrue(0.0 <= prob <= 1.0)

            # MLFilterEngine이 배포 모델을 집어 실 추론 경로를 사용
            engine = MLFilterEngine()
            self.assertEqual(engine._get_deployed_model().id, artifact.id)
            p_d, risk_d, exp_d = engine._model_inference(
                artifact, dataset.items.first().feature_payload
            )
            self.assertTrue(Decimal("0") <= p_d <= Decimal("1"))
            self.assertTrue(Decimal("0") <= risk_d <= Decimal("1"))


from django.core.management import call_command
from io import StringIO
from apps.system.models import ModelDeployment
from core.ml.training import deploy_artifact


class DeploymentTestCase(TestCase):
    def _artifact(self, version):
        return ModelArtifact.objects.create(
            model_name="lgb_signal_filter",
            version=version,
            artifact_uri=f"/tmp/{version}.pkl",
            artifact_checksum="x",
            status=ModelArtifact.Status.READY,
        )

    def test_deploy_retires_previous(self):
        a1 = self._artifact("v1")
        a2 = self._artifact("v2")

        d1 = deploy_artifact(a1)
        self.assertEqual(d1.status, ModelDeployment.Status.ACTIVE)
        a1.refresh_from_db()
        self.assertEqual(a1.status, ModelArtifact.Status.DEPLOYED)

        d2 = deploy_artifact(a2)
        # 이전 배포/아티팩트는 종료
        d1.refresh_from_db()
        a1.refresh_from_db()
        a2.refresh_from_db()
        self.assertEqual(d1.status, ModelDeployment.Status.RETIRED)
        self.assertEqual(a1.status, ModelArtifact.Status.RETIRED)
        self.assertEqual(a2.status, ModelArtifact.Status.DEPLOYED)
        # 활성 배포는 정확히 1개
        self.assertEqual(
            ModelDeployment.objects.filter(
                status=ModelDeployment.Status.ACTIVE
            ).count(),
            1,
        )


class TrainCommandTestCase(TestCase):
    def setUp(self):
        self.stock = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="005930", name="삼성전자"
        )
        base = datetime(2024, 1, 2, 9, 0, tzinfo=dt_timezone.utc)
        price = 70000
        for i in range(60):
            price += 200 if (i % 10) < 5 else -200
            p = Decimal(str(price))
            Candle.objects.create(
                stock=self.stock,
                timeframe=Candle.Timeframe.MIN_1,
                opened_at=base + timedelta(minutes=i),
                open_price=p,
                high_price=p,
                low_price=p,
                close_price=p,
                volume=Decimal("1000"),
                source="test",
            )

    def test_train_command_deploys(self):
        with tempfile.TemporaryDirectory() as d:
            with self.settings(MODEL_ARTIFACT_DIR=d):
                call_command(
                    "train_daytrading_model",
                    "--symbol",
                    "005930",
                    "--model-version",
                    "v1",
                    "--horizon",
                    "5",
                    "--upper",
                    "0.002",
                    "--lower",
                    "0.002",
                    "--deploy",
                    stdout=StringIO(),
                )
        artifact = ModelArtifact.objects.get(
            model_name="lgb_signal_filter", version="v1"
        )
        self.assertEqual(artifact.status, ModelArtifact.Status.DEPLOYED)
        self.assertTrue(
            ModelDeployment.objects.filter(
                model_artifact=artifact, status=ModelDeployment.Status.ACTIVE
            ).exists()
        )
