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


from core.ml.wfv import compute_uniqueness_weights, run_dataset_walk_forward


class UniquenessWeightTestCase(SimpleTestCase):
    def test_isolated_sample_gets_full_weight(self):
        # (5,6)은 겹치지 않음 → 1.0, (0,2)/(1,3)은 겹침 → <1.0
        weights = compute_uniqueness_weights([(0, 2), (1, 3), (5, 6)])
        self.assertEqual(weights[2], 1.0)
        self.assertLess(weights[0], 1.0)
        self.assertLess(weights[1], 1.0)

    def test_empty(self):
        self.assertEqual(compute_uniqueness_weights([]), [])

    def test_train_accepts_sample_weight(self):
        feats = [{"x": 1.0} if i % 2 == 0 else {"x": -1.0} for i in range(20)]
        labels = [1 if i % 2 == 0 else 0 for i in range(20)]
        w = [0.5] * 20
        pred, _ = LightGBMPredictor.train(feats, labels, num_boost_round=10, sample_weight=w)
        self.assertIsNotNone(pred.booster)


class DatasetWalkForwardTestCase(TestCase):
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
                stock=self.stock, timeframe=Candle.Timeframe.MIN_1,
                opened_at=base + timedelta(minutes=i),
                open_price=p, high_price=p, low_price=p, close_price=p,
                volume=Decimal("1000"), source="test",
            )

    def test_run_dataset_walk_forward(self):
        dataset = build_dataset_from_candles(
            self.stock, name="s", version="v1",
            config=DatasetConfig(upper=0.002, lower=0.002, horizon=5, min_history=20, cost=0.0),
        )
        result = run_dataset_walk_forward(dataset, train_size=15, test_size=5)
        self.assertGreater(result["n_folds"], 0)
        self.assertEqual(len(result["folds"]), result["n_folds"])
        self.assertTrue(0.0 <= result["auc_mean"] <= 1.0)
        for f in result["folds"]:
            self.assertIn("auc", f)
            self.assertEqual(f["n_test"], 5)


import numpy as _np
from core.ml.diagnostics import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    pbo_cscv,
    probabilistic_sharpe_ratio,
)


class DiagnosticsTestCase(SimpleTestCase):
    def test_psr_midpoint_and_monotonic(self):
        # 관측 샤프 = 기준이면 ~0.5
        self.assertAlmostEqual(probabilistic_sharpe_ratio(0.1, 100, 0.1), 0.5, places=6)
        # 샤프가 클수록 PSR 증가
        self.assertGreater(
            probabilistic_sharpe_ratio(0.2, 100, 0.0),
            probabilistic_sharpe_ratio(0.05, 100, 0.0),
        )

    def test_expected_max_sharpe_increases_with_trials(self):
        self.assertGreater(
            expected_max_sharpe(100, 0.01), expected_max_sharpe(5, 0.01)
        )

    def test_deflated_sharpe_drops_with_more_trials(self):
        dsr_few = deflated_sharpe_ratio(0.3, 250, n_trials=2, sr_variance=0.01)
        dsr_many = deflated_sharpe_ratio(0.3, 250, n_trials=200, sr_variance=0.01)
        self.assertGreater(dsr_few, dsr_many)

    def test_pbo_low_for_genuine_skill(self):
        # config0가 모든 블록에서 우월 → IS 최적이 OS에서도 최상 → PBO 낮음
        T, N = 40, 4
        M = _np.random.default_rng(0).normal(0, 0.001, size=(T, N))
        M[:, 0] += 0.05  # 지속 우월
        self.assertLess(pbo_cscv(M, n_splits=6), 0.1)

    def test_pbo_range_and_validation(self):
        M = _np.random.default_rng(1).normal(0, 0.01, size=(30, 5))
        pbo = pbo_cscv(M, n_splits=6)
        self.assertTrue(0.0 <= pbo <= 1.0)
        with self.assertRaises(ValueError):
            pbo_cscv(_np.zeros((10, 1)))  # N<2


from core.ml.calibration import (
    IsotonicCalibrator,
    PlattCalibrator,
    calibrator_from_dict,
    fit_calibrator,
)


class CalibrationTestCase(SimpleTestCase):
    def _data(self):
        # 점수가 높을수록 양성 비율↑ (보정 가능한 신호)
        scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
        labels = [0, 0, 0, 0, 1, 0, 1, 1, 1, 1]
        return scores, labels

    def test_isotonic_monotonic_and_ranged(self):
        cal = IsotonicCalibrator.fit(*self._data())
        lo, hi = cal.predict(0.15), cal.predict(0.9)
        self.assertLessEqual(lo, hi)  # 단조
        for s in (0.0, 0.5, 1.0):
            self.assertTrue(0.0 <= cal.predict(s) <= 1.0)

    def test_platt_ranged_and_serializable(self):
        cal = PlattCalibrator.fit(*self._data())
        self.assertGreater(cal.predict(0.9), cal.predict(0.1))  # 단조 증가
        restored = calibrator_from_dict(cal.to_dict())
        self.assertAlmostEqual(restored.predict(0.7), cal.predict(0.7), places=9)

    def test_isotonic_roundtrip(self):
        cal = IsotonicCalibrator.fit(*self._data())
        restored = calibrator_from_dict(cal.to_dict())
        self.assertEqual(restored.predict(0.6), cal.predict(0.6))

    def test_predictor_applies_and_persists_calibrator(self):
        feats = [{"x": 1.0} if i % 2 == 0 else {"x": -1.0} for i in range(40)]
        labels = [1 if i % 2 == 0 else 0 for i in range(40)]
        pred, _ = LightGBMPredictor.train(feats, labels, num_boost_round=20)
        raw_hi = float(pred.booster.predict(_np_vec(pred, {"x": 1.0}))[0])
        pred.fit_calibration(feats, labels, method="isotonic")
        cal_hi = pred.predict_proba({"x": 1.0})
        self.assertTrue(0.0 <= cal_hi <= 1.0)

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.pkl")
            pred.save(path)
            loaded = LightGBMPredictor.load(path)
            self.assertIsNotNone(loaded.calibrator)
            self.assertAlmostEqual(loaded.predict_proba({"x": 1.0}), cal_hi, places=6)


def _np_vec(pred, d):
    from core.ml.predictor import _vectorize
    return _vectorize([d], pred.feature_names)


class MetaLabelingTestCase(TestCase):
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
                stock=self.stock, timeframe=Candle.Timeframe.MIN_1,
                opened_at=base + timedelta(minutes=i),
                open_price=p, high_price=p, low_price=p, close_price=p,
                volume=Decimal("1000"), source="test",
            )

    def test_meta_labeling_gates_samples(self):
        cfg = DatasetConfig(upper=0.002, lower=0.002, horizon=5, min_history=20, cost=0.0)
        full = build_dataset_from_candles(self.stock, name="full", version="v1", config=cfg)

        # 1차 신호: 최근 5봉 상승일 때만 진입
        def primary(feats, index):
            return feats.get("ret_5", 0.0) > 0

        meta = build_dataset_from_candles(
            self.stock, name="meta", version="v1", config=cfg, primary_signal_fn=primary
        )

        self.assertTrue(meta.label_definition["meta_labeling"])
        self.assertGreater(meta.items.count(), 0)
        # 게이팅으로 전체보다 표본 수 감소
        self.assertLess(meta.items.count(), full.items.count())
        # 메타 표본에는 primary_signal 피처가 실림
        self.assertEqual(meta.items.first().feature_payload.get("primary_signal"), 1.0)
