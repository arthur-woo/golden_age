"""
Account 도메인 모델

acc_account  : 증권계좌
acc_broker_token : Broker API 토큰
"""

from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class Account(models.Model):
    """
    증권계좌 (acc_account)

    하나의 User는 여러 Account를 가질 수 있다.
    계좌별로 독립적인 Trader를 운용한다.
    """

    class Broker(models.TextChoices):
        KIS = "KIS", "한국투자증권"

    class AccountType(models.TextChoices):
        LIVE = "LIVE", "실계좌"
        PAPER = "PAPER", "모의계좌"

    class InvestmentProfile(models.TextChoices):
        AGGRESSIVE = "AGGRESSIVE", "공격형"
        NEUTRAL = "NEUTRAL", "중립형"
        CONSERVATIVE = "CONSERVATIVE", "안정형"

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="accounts")
    broker = models.CharField(max_length=32, choices=Broker.choices, default=Broker.KIS)
    account_type = models.CharField(max_length=16, choices=AccountType.choices)
    account_number = models.CharField(max_length=64)
    name = models.CharField(max_length=100)
    investment_profile = models.CharField(
        max_length=32,
        choices=InvestmentProfile.choices,
        default=InvestmentProfile.NEUTRAL,
    )
    # 민감 정보 - 애플리케이션 레이어에서 암호화하여 저장
    app_key_encrypted = models.TextField()
    app_secret_encrypted = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "acc_account"
        verbose_name = "계좌"
        verbose_name_plural = "계좌 목록"
        unique_together = [("user", "broker", "account_type", "account_number")]
        indexes = [
            models.Index(fields=["user", "is_active"], name="acc_account_user_active_idx"),
            models.Index(fields=["broker", "account_type"], name="acc_account_broker_type_idx"),
        ]

    def __str__(self) -> str:
        return f"[{self.get_account_type_display()}] {self.name} ({self.account_number})"


class BrokerToken(models.Model):
    """
    Broker API 토큰 (acc_broker_token)

    토큰은 만료/재발급 주기가 짧으므로 Account와 분리한다.
    """

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="tokens")
    access_token_encrypted = models.TextField()
    token_type = models.CharField(max_length=32)
    expires_at = models.DateTimeField()
    issued_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "acc_broker_token"
        verbose_name = "Broker 토큰"
        verbose_name_plural = "Broker 토큰 목록"
        indexes = [
            models.Index(fields=["account", "expires_at"], name="acc_broker_token_acct_exp_idx"),
            models.Index(fields=["revoked_at"], name="acc_broker_token_revoked_idx"),
        ]

    def __str__(self) -> str:
        return f"Token({self.account}) - expires: {self.expires_at}"

    @property
    def is_valid(self) -> bool:
        """토큰이 유효한지 확인 (만료 및 폐기 여부)."""
        from django.utils import timezone

        return self.revoked_at is None and self.expires_at > timezone.now()


class ExecutionRun(models.Model):
    """
    Account 단위 실행 결과 (acc_execution_run)
    """
    class RunType(models.TextChoices):
        SCHEDULED = "SCHEDULED", "스케줄됨"
        MANUAL = "MANUAL", "수동"

    class Status(models.TextChoices):
        RUNNING = "RUNNING", "실행중"
        SUCCESS = "SUCCESS", "성공"
        FAILED = "FAILED", "실패"

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="execution_runs")
    run_type = models.CharField(max_length=32, choices=RunType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(help_text="시작 시각")
    finished_at = models.DateTimeField(null=True, blank=True, help_text="종료 시각")
    error_message = models.TextField(blank=True, help_text="실패 사유")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "acc_execution_run"
        verbose_name = "계좌 실행 결과"
        verbose_name_plural = "계좌 실행 결과 목록"
        indexes = [
            models.Index(fields=["account", "-started_at"], name="acc_exec_run_acc_start_idx"),
            models.Index(fields=["status", "-started_at"], name="acc_exec_run_sts_start_idx"),
        ]

    def __str__(self):
        return f"Run {self.id} for {self.account} ({self.status})"


class CashLedger(models.Model):
    """
    현금 변동 이력 (acc_cash_ledger)
    """
    class EventType(models.TextChoices):
        DEPOSIT = "DEPOSIT", "입금"
        WITHDRAWAL = "WITHDRAWAL", "출금"
        BUY = "BUY", "매수"
        SELL = "SELL", "매도"
        FEE = "FEE", "수수료"
        TAX = "TAX", "세금"
        ADJUSTMENT = "ADJUSTMENT", "조정"

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="cash_ledgers")
    trade_execution = models.ForeignKey("order.TradeExecution", on_delete=models.SET_NULL, null=True, blank=True, related_name="cash_ledgers")
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2, help_text="증감 금액")
    currency = models.CharField(max_length=8, default="KRW")
    reason = models.TextField(blank=True, help_text="사유")
    occurred_at = models.DateTimeField(help_text="발생 시각")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "acc_cash_ledger"
        verbose_name = "현금 원장"
        verbose_name_plural = "현금 원장 목록"
        indexes = [
            models.Index(fields=["account", "-occurred_at"], name="acc_cash_acc_occ_idx"),
            models.Index(fields=["trade_execution"], name="acc_cash_trade_exec_idx"),
        ]


class PositionLedger(models.Model):
    """
    보유 수량 변동 이력 (acc_position_ledger)
    """
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="position_ledgers")
    stock = models.ForeignKey("stock.Stock", on_delete=models.PROTECT, related_name="position_ledgers")
    trade_execution = models.ForeignKey("order.TradeExecution", on_delete=models.SET_NULL, null=True, blank=True, related_name="position_ledgers")
    quantity_delta = models.DecimalField(max_digits=18, decimal_places=8, help_text="수량 증감")
    price = models.DecimalField(max_digits=18, decimal_places=2, help_text="기준 가격")
    reason = models.TextField(blank=True, help_text="사유")
    occurred_at = models.DateTimeField(help_text="발생 시각")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "acc_position_ledger"
        verbose_name = "포지션 원장"
        verbose_name_plural = "포지션 원장 목록"
        indexes = [
            models.Index(fields=["account", "stock", "-occurred_at"], name="acc_pos_acc_stk_occ_idx"),
            models.Index(fields=["trade_execution"], name="acc_pos_trade_exec_idx"),
        ]


class BalanceSnapshot(models.Model):
    """
    조회 성능을 위한 계좌 잔고 스냅샷 (acc_balance_snapshot)
    """
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="balance_snapshots")
    cash_balance = models.DecimalField(max_digits=18, decimal_places=2, help_text="현금 잔고")
    total_asset_value = models.DecimalField(max_digits=18, decimal_places=2, help_text="총 평가 금액")
    snapshot_payload = models.JSONField(default=dict, blank=True, help_text="상세 스냅샷")
    snapshotted_at = models.DateTimeField(help_text="스냅샷 시각")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "acc_balance_snapshot"
        verbose_name = "잔고 스냅샷"
        verbose_name_plural = "잔고 스냅샷 목록"
        indexes = [
            models.Index(fields=["account", "-snapshotted_at"], name="acc_bal_snap_acc_time_idx"),
        ]
