"""
Trading 도메인 모델

trd_trader          : 자동매매 봇
trd_strategy        : Strategy 식별자
trd_strategy_version: Strategy 구현 버전
trd_trader_strategy : Trader ↔ StrategyVersion 연결 (최대 2개, slot 1/2)
"""

from django.contrib.auth import get_user_model
from django.db import models

from apps.account.models import Account

User = get_user_model()


class Trader(models.Model):
    """
    자동매매 봇 (trd_trader)

    하나의 Account에 여러 Trader를 등록할 수 있다.
    Trader는 최대 2개의 Strategy를 가진다.
    Trader는 Strategy Score를 조합하여 최종 매매 방향을 결정한다.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "활성"
        PAUSED = "PAUSED", "일시정지"
        STOPPED = "STOPPED", "중지"

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="traders")
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=64, help_text="계좌 내 고유 코드")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    # Trader 기본 파라미터 - Market Analyzer가 조정함
    position_size_ratio = models.DecimalField(
        max_digits=10, decimal_places=6, default=0.1,
        help_text="기본 포지션 비율 (0.0 ~ 1.0)"
    )
    entry_threshold = models.DecimalField(
        max_digits=10, decimal_places=6, default=0.5,
        help_text="기본 진입 기준 Score"
    )
    stop_loss_ratio = models.DecimalField(
        max_digits=10, decimal_places=6, default=0.03,
        help_text="손절 기준 비율"
    )
    take_profit_ratio = models.DecimalField(
        max_digits=10, decimal_places=6, default=0.05,
        help_text="익절 기준 비율"
    )
    max_exposure_ratio = models.DecimalField(
        max_digits=10, decimal_places=6, default=0.3,
        help_text="최대 노출 비율"
    )
    ml_filter_enabled = models.BooleanField(default=False, help_text="ML Filter 사용 여부")
    config_payload = models.JSONField(default=dict, blank=True, help_text="Trader별 추가 설정")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "trd_trader"
        verbose_name = "Trader"
        verbose_name_plural = "Trader 목록"
        unique_together = [("account", "code")]
        indexes = [
            models.Index(fields=["account", "status"], name="trd_trader_account_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.account})"


class Strategy(models.Model):
    """
    Strategy 논리적 식별자 (trd_strategy)

    Strategy 코드는 데이터베이스에 저장하지 않는다.
    DB에는 Strategy 식별자, 버전, 설정, 판단 결과만 저장한다.
    개발자별 Strategy는 namespace로 구분한다.
    """

    owner = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="strategies",
        help_text="Strategy 소유 개발자"
    )
    namespace = models.CharField(max_length=64, help_text="개발자 또는 전략 네임스페이스")
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=64, help_text="Strategy 고유 코드")
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "trd_strategy"
        verbose_name = "Strategy"
        verbose_name_plural = "Strategy 목록"
        unique_together = [("namespace", "code")]
        indexes = [
            models.Index(fields=["owner", "is_active"], name="trd_strategy_owner_active_idx"),
            models.Index(fields=["namespace", "is_active"], name="trd_strategy_ns_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.namespace}.{self.code}"


class StrategyVersion(models.Model):
    """
    Strategy 구현 버전 (trd_strategy_version)

    Strategy 실행 단위는 Version으로 고정한다.
    개발자별 Strategy 변경이 다른 Strategy에 영향을 주지 않는다.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "초안"
        ACTIVE = "ACTIVE", "활성"
        RETIRED = "RETIRED", "종료"

    strategy = models.ForeignKey(Strategy, on_delete=models.PROTECT, related_name="versions")
    version = models.CharField(max_length=32, help_text="사람이 읽는 버전 (예: v1.0.0)")
    module_path = models.CharField(max_length=255, help_text="Python module path")
    class_name = models.CharField(max_length=100, help_text="Strategy class name")
    commit_hash = models.CharField(max_length=64, blank=True, help_text="코드 커밋 식별자")
    config_schema = models.JSONField(default=dict, blank=True, help_text="설정 스키마")
    default_config = models.JSONField(default=dict, blank=True, help_text="기본 설정")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "trd_strategy_version"
        verbose_name = "Strategy 버전"
        verbose_name_plural = "Strategy 버전 목록"
        unique_together = [("strategy", "version")]
        indexes = [
            models.Index(fields=["strategy", "status"], name="trd_sv_strategy_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.strategy} {self.version}"


class TraderStrategy(models.Model):
    """
    Trader ↔ StrategyVersion 연결 (trd_trader_strategy)

    Trader는 최대 2개의 활성 Strategy를 가진다 (slot 1 또는 2).
    weight는 Score 조합 시 사용하는 가중치다.
    """

    class Slot(models.IntegerChoices):
        FIRST = 1, "Slot 1"
        SECOND = 2, "Slot 2"

    trader = models.ForeignKey(Trader, on_delete=models.CASCADE, related_name="trader_strategies")
    strategy_version = models.ForeignKey(
        StrategyVersion, on_delete=models.PROTECT, related_name="trader_strategies"
    )
    slot = models.SmallIntegerField(choices=Slot.choices, help_text="Slot 1 또는 2")
    weight = models.DecimalField(
        max_digits=10, decimal_places=6, default=1.0,
        help_text="Score 조합 가중치"
    )
    config_payload = models.JSONField(default=dict, blank=True, help_text="Trader에 연결된 Strategy 설정")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "trd_trader_strategy"
        verbose_name = "Trader-Strategy 연결"
        verbose_name_plural = "Trader-Strategy 연결 목록"
        indexes = [
            models.Index(
                fields=["strategy_version", "is_active"],
                name="trd_ts_sv_active_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(weight__gte=0), name="trd_ts_weight_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.trader} - Slot {self.slot}: {self.strategy_version}"
