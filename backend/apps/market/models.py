"""
Market 도메인 모델

mkt_candle            : 분봉/일봉 시세 데이터
mkt_regime_snapshot   : Market Analyzer 판단 결과
mkt_feature_snapshot  : 모델 입력/로깅용 Feature 스냅샷
"""

from django.db import models
from apps.stock.models import Stock

class Candle(models.Model):
    """
    시세 데이터 (mkt_candle)
    분봉/일봉 데이터를 Append Only로 저장
    """
    class Timeframe(models.TextChoices):
        MIN_1 = "1m", "1분"
        MIN_5 = "5m", "5분"
        MIN_15 = "15m", "15분"
        DAY_1 = "1d", "1일"

    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name="candles")
    timeframe = models.CharField(max_length=16, choices=Timeframe.choices)
    opened_at = models.DateTimeField(help_text="캔들 시작 시각")
    open_price = models.DecimalField(max_digits=18, decimal_places=2)
    high_price = models.DecimalField(max_digits=18, decimal_places=2)
    low_price = models.DecimalField(max_digits=18, decimal_places=2)
    close_price = models.DecimalField(max_digits=18, decimal_places=2)
    volume = models.DecimalField(max_digits=18, decimal_places=8)
    source = models.CharField(max_length=32, help_text="데이터 출처")
    raw_payload = models.JSONField(default=dict, blank=True, help_text="원본 응답")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mkt_candle"
        verbose_name = "시세 캔들"
        verbose_name_plural = "시세 캔들 목록"
        unique_together = [("stock", "timeframe", "opened_at", "source")]
        indexes = [
            models.Index(fields=["stock", "timeframe", "-opened_at"], name="mkt_candle_stk_tf_open_idx"),
        ]

    def __str__(self):
        return f"{self.stock.symbol} {self.timeframe} [{self.opened_at}]"


class RegimeSnapshot(models.Model):
    """
    시장 국면 판단 스냅샷 (mkt_regime_snapshot)
    """
    class Regime(models.TextChoices):
        BULL = "BULL", "상승장"
        SIDEWAYS = "SIDEWAYS", "횡보장"
        BEAR = "BEAR", "하락장"

    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, null=True, blank=True, related_name="regime_snapshots")
    regime = models.CharField(max_length=16, choices=Regime.choices)
    confidence_score = models.DecimalField(max_digits=10, decimal_places=6, help_text="판단 신뢰도")
    parameter_payload = models.JSONField(default=dict, blank=True, help_text="Trader/Strategy 전달용 시장 파라미터")
    reason = models.TextField(blank=True, help_text="판단 근거")
    analyzed_at = models.DateTimeField(help_text="분석 시각")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mkt_regime_snapshot"
        verbose_name = "시장 국면 스냅샷"
        verbose_name_plural = "시장 국면 스냅샷 목록"
        indexes = [
            models.Index(fields=["stock", "-analyzed_at"], name="mkt_regime_stk_analyzed_idx"),
            models.Index(fields=["regime", "-analyzed_at"], name="mkt_regime_reg_analyzed_idx"),
        ]

    def __str__(self):
        return f"[{self.regime}] ({self.analyzed_at})"


class FeatureSnapshot(models.Model):
    """
    Feature Vector 스냅샷 (mkt_feature_snapshot)
    Strategy / ML 파이프라인 입력 데이터를 기록
    """
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name="feature_snapshots")
    timeframe = models.CharField(max_length=16, help_text="Feature 기준 timeframe")
    feature_payload = models.JSONField(default=dict, help_text="Feature Vector")
    source_payload = models.JSONField(default=dict, blank=True, help_text="원천 데이터 참조 요약")
    captured_at = models.DateTimeField(help_text="Feature 기준 시각")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mkt_feature_snapshot"
        verbose_name = "Feature 스냅샷"
        verbose_name_plural = "Feature 스냅샷 목록"
        indexes = [
            models.Index(fields=["stock", "timeframe", "-captured_at"], name="mkt_feat_stk_tf_cap_idx"),
        ]

    def __str__(self):
        return f"Feature {self.stock.symbol} {self.timeframe} [{self.captured_at}]"
