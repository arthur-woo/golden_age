from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import (
    ModelArtifact,
    ModelDeployment,
    TrainingDataset,
    TrainingDatasetItem,
)


@admin.register(ModelDeployment)
class ModelDeploymentAdmin(ModelAdmin):
    list_display = (
        "id",
        "model_artifact",
        "trader",
        "status",
        "deployed_at",
        "retired_at",
    )
    list_filter = ("status",)
    readonly_fields = ("created_at",)
    list_select_related = ("model_artifact", "trader")


@admin.register(ModelArtifact)
class ModelArtifactAdmin(ModelAdmin):
    list_display = ("model_name", "version", "status", "trained_at", "created_at")
    list_filter = ("status", "model_name")
    search_fields = ("model_name", "version")
    readonly_fields = ("created_at",)


@admin.register(TrainingDataset)
class TrainingDatasetAdmin(ModelAdmin):
    list_display = (
        "name",
        "version",
        "status",
        "source_started_at",
        "source_ended_at",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("name", "version")
    readonly_fields = ("created_at",)


@admin.register(TrainingDatasetItem)
class TrainingDatasetItemAdmin(ModelAdmin):
    list_display = (
        "id",
        "training_dataset",
        "label",
        "realized_return",
        "feature_snapshot",
        "created_at",
    )
    list_filter = ("label", "training_dataset")
    readonly_fields = ("created_at",)
    list_select_related = ("training_dataset", "feature_snapshot")
