from django.db import models


class ModelArtifact(models.Model):
    """
    학습된 모델 산출물 (sys_model_artifact)
    """

    class Status(models.TextChoices):
        TRAINING = "TRAINING", "학습중"
        READY = "READY", "준비완료"
        DEPLOYED = "DEPLOYED", "배포됨"
        RETIRED = "RETIRED", "종료됨"

    model_name = models.CharField(max_length=100)
    version = models.CharField(max_length=32)
    artifact_uri = models.TextField(help_text="모델 파일 스토리지 경로")
    artifact_checksum = models.CharField(max_length=128, help_text="파일 검증용 체크섬")
    training_dataset_id = models.BigIntegerField(
        null=True, blank=True, help_text="학습 데이터셋 ID"
    )
    metrics_payload = models.JSONField(
        default=dict, blank=True, help_text="학습 및 검증 평가지표"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.READY
    )
    trained_at = models.DateTimeField(null=True, blank=True, help_text="학습 완료 시각")
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True, help_text="사용 중지 시각")

    class Meta:
        db_table = "sys_model_artifact"
        verbose_name = "모델 산출물"
        verbose_name_plural = "모델 산출물 목록"
        unique_together = [("model_name", "version")]
        indexes = [
            models.Index(
                fields=["model_name", "status"], name="sys_model_art_name_stat_idx"
            ),
        ]

    def __str__(self):
        return f"{self.model_name} ({self.version}) - {self.status}"


class ModelDeployment(models.Model):
    """
    모델 배포 이력 (sys_model_deployment)

    어떤 ModelArtifact가 언제부터/어디(Trader)에 활성 배포되었는지 추적한다.
    trader=None 이면 계정/전역 기본 배포를 의미한다.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "활성"
        RETIRED = "RETIRED", "종료"

    model_artifact = models.ForeignKey(
        "ModelArtifact", on_delete=models.CASCADE, related_name="deployments"
    )
    trader = models.ForeignKey(
        "trading.Trader",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="model_deployments",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    deployed_at = models.DateTimeField(help_text="배포 시각")
    retired_at = models.DateTimeField(null=True, blank=True, help_text="종료 시각")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sys_model_deployment"
        verbose_name = "모델 배포"
        verbose_name_plural = "모델 배포 목록"
        indexes = [
            models.Index(fields=["trader", "status"], name="sys_model_dep_trader_idx"),
            models.Index(
                fields=["model_artifact", "status"], name="sys_model_dep_art_idx"
            ),
        ]

    def __str__(self):
        return f"Deploy {self.model_artifact} -> {self.trader or '전역'} ({self.status})"


class TrainingDataset(models.Model):
    """
    학습 데이터셋 버전 (sys_training_dataset)

    Dataset은 생성 후 수정하지 않는다(불변). 재현성을 위해 생성 기간·정의를 함께 저장한다.
    """

    class Status(models.TextChoices):
        BUILDING = "BUILDING", "생성중"
        READY = "READY", "준비완료"
        RETIRED = "RETIRED", "종료됨"

    name = models.CharField(max_length=100)
    version = models.CharField(max_length=32)
    source_started_at = models.DateTimeField(help_text="학습 데이터 시작 시각")
    source_ended_at = models.DateTimeField(help_text="학습 데이터 종료 시각")
    feature_definition = models.JSONField(
        default=dict, blank=True, help_text="Feature 정의"
    )
    label_definition = models.JSONField(
        default=dict, blank=True, help_text="Label 정의(배리어/비용 등)"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.BUILDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True, help_text="생성 완료 시각")

    class Meta:
        db_table = "sys_training_dataset"
        verbose_name = "학습 데이터셋"
        verbose_name_plural = "학습 데이터셋 목록"
        unique_together = [("name", "version")]
        indexes = [
            models.Index(
                fields=["status", "-created_at"], name="sys_train_ds_status_idx"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.version}) - {self.status}"


class TrainingDatasetItem(models.Model):
    """
    학습 데이터셋 개별 샘플 (sys_training_dataset_item)

    각 샘플은 하나의 Feature 스냅샷과 그에 대한 라벨(수익/손실)로 구성된다.
    실현 거래(trader_decision_log)에서 생성될 수도, 과거 캔들 기반 오프라인 라벨링으로
    생성될 수도 있으므로 trader_decision_log는 nullable로 둔다(콜드스타트 지원).
    """

    training_dataset = models.ForeignKey(
        TrainingDataset, on_delete=models.CASCADE, related_name="items"
    )
    trader_decision_log = models.ForeignKey(
        "trading.DecisionLog",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="training_items",
        help_text="실현 거래 기반 샘플일 때의 최종 판단 로그",
    )
    trade_execution = models.ForeignKey(
        "order.TradeExecution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="training_items",
    )
    feature_snapshot = models.ForeignKey(
        "market.FeatureSnapshot",
        on_delete=models.CASCADE,
        related_name="training_items",
    )
    label = models.SmallIntegerField(help_text="1 = 수익, 0 = 손실")
    realized_return = models.DecimalField(
        max_digits=10, decimal_places=6, help_text="비용 차감 실현 수익률"
    )
    feature_payload = models.JSONField(
        default=dict, blank=True, help_text="학습용 Feature"
    )
    label_payload = models.JSONField(default=dict, blank=True, help_text="Label 생성 근거")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sys_training_dataset_item"
        verbose_name = "학습 샘플"
        verbose_name_plural = "학습 샘플 목록"
        indexes = [
            models.Index(fields=["training_dataset"], name="sys_train_item_ds_idx"),
            models.Index(fields=["label"], name="sys_train_item_label_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(label__in=(0, 1)), name="sys_train_item_label_binary"
            ),
        ]

    def __str__(self):
        return f"Item {self.id} (label={self.label}, ret={self.realized_return})"
