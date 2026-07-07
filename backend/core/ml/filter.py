import logging
from decimal import Decimal
from typing import Optional

from django.utils import timezone

from apps.system.models import ModelArtifact
from apps.trading.models import TraderExecutionRun, MLOutputLog
from apps.market.models import FeatureSnapshot

logger = logging.getLogger(__name__)


class MLFilterEngine:
    """
    ML Filter 추론 및 예측 결과(trd_ml_output_log) 기록 엔진.

    배포(DEPLOYED)된 LightGBM ModelArtifact 가 있으면 실 모델로 추론하고,
    없거나 로딩 실패 시 규칙 기반 Mock 추론으로 폴백한다(콜드스타트/테스트 지원).
    """

    def __init__(self, model_name: str = "lgb_signal_filter"):
        self.model_name = model_name

    def _get_deployed_model(self) -> Optional[ModelArtifact]:
        """배포 상태인 최신 모델 로드"""
        return (
            ModelArtifact.objects.filter(
                model_name=self.model_name,
                status=ModelArtifact.Status.DEPLOYED,
            )
            .order_by("-created_at")
            .first()
        )

    def _model_inference(
        self, model: ModelArtifact, feature_payload: dict
    ) -> tuple[Decimal, Decimal, Decimal]:
        """실 LightGBM 모델 추론. (trade_probability, risk_score, expected_return)."""
        # 지연 임포트: lightgbm 은 실 모델 경로에서만 필요
        from core.ml.training import load_predictor_from_artifact

        predictor = load_predictor_from_artifact(model)
        p = predictor.predict_proba(feature_payload)
        trade_probability = Decimal(str(round(p, 6)))
        risk_score = Decimal(str(round(1.0 - p, 6)))
        # 기대수익 근사: 확률을 [-1, 1] 스케일의 방향 스코어로 변환(운영 시 캘리브레이션)
        expected_return = Decimal(str(round((2.0 * p - 1.0) * 0.01, 6)))
        return trade_probability, risk_score, expected_return

    def _mock_inference(
        self, feature_payload: dict
    ) -> tuple[Decimal, Decimal, Decimal]:
        """규칙 기반 폴백 추론. RSI 극단값을 리스크로 매핑한다."""
        trade_probability = Decimal("0.75")
        risk_score = Decimal("0.20")
        expected_return = Decimal("0.02")

        rsi_val = Decimal(str(feature_payload.get("rsi", "50.0")))
        if rsi_val <= 20 or rsi_val >= 80:
            trade_probability = Decimal("0.45")
            risk_score = Decimal("0.75")  # 높은 리스크로 필터링 트리거
            expected_return = Decimal("-0.01")
        elif rsi_val >= 60:
            trade_probability = Decimal("0.55")
            risk_score = Decimal("0.40")
            expected_return = Decimal("0.005")

        # 테스트/디버그용 직접 오버라이드
        if "mock_trade_probability" in feature_payload:
            trade_probability = Decimal(str(feature_payload["mock_trade_probability"]))
        if "mock_risk_score" in feature_payload:
            risk_score = Decimal(str(feature_payload["mock_risk_score"]))
        if "mock_expected_return" in feature_payload:
            expected_return = Decimal(str(feature_payload["mock_expected_return"]))

        return trade_probability, risk_score, expected_return

    def run_inference(
        self,
        trader_run: TraderExecutionRun,
        feature_snapshot: Optional[FeatureSnapshot] = None,
    ) -> MLOutputLog:
        """
        FeatureSnapshot을 기반으로 추론하고 결과를 trd_ml_output_log에 기록한다.
        """
        model = self._get_deployed_model()
        input_payload = feature_snapshot.feature_payload if feature_snapshot else {}

        source = "mock"
        prob = risk = expected = None
        if model and feature_snapshot:
            try:
                prob, risk, expected = self._model_inference(model, input_payload)
                source = "model"
            except Exception as e:  # noqa: BLE001 - 로딩/추론 실패 시 폴백
                logger.exception("ML 모델 추론 실패, Mock으로 폴백: %s", e)

        if prob is None:
            prob, risk, expected = self._mock_inference(input_payload)

        ml_log = MLOutputLog.objects.create(
            trader_run=trader_run,
            model_artifact=model,
            trade_probability=prob,
            risk_score=risk,
            expected_return=expected,
            input_payload=input_payload,
            output_payload={
                "trade_probability": str(prob),
                "risk_score": str(risk),
                "expected_return": str(expected),
                "source": source,
                "model_used": str(model) if model else "None",
            },
            created_at=timezone.now(),
        )

        logger.info(
            "ML Filter (%s): probability=%s, risk=%s, expected_return=%s",
            source,
            prob,
            risk,
            expected,
        )
        return ml_log
