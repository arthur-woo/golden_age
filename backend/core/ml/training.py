"""
학습 파이프라인 (Phase 5-5)

TrainingDataset(sys_training_dataset_item) 을 읽어 LightGBM 모델을 학습하고,
모델 파일을 저장한 뒤 ModelArtifact(sys_model_artifact) 메타데이터를 기록한다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from django.conf import settings
from django.utils import timezone

from apps.system.models import ModelArtifact, ModelDeployment, TrainingDataset
from core.ml.predictor import LightGBMPredictor


def deploy_artifact(artifact: ModelArtifact, trader=None) -> ModelDeployment:
    """
    아티팩트를 활성 배포한다. 같은 model_name의 기존 활성 배포는 자동 종료(RETIRED)한다.

    MLFilterEngine 은 ModelArtifact.status=DEPLOYED 를 사용하므로 상태도 함께 갱신한다.
    """
    now = timezone.now()
    ModelDeployment.objects.filter(
        status=ModelDeployment.Status.ACTIVE,
        model_artifact__model_name=artifact.model_name,
    ).update(status=ModelDeployment.Status.RETIRED, retired_at=now)
    ModelArtifact.objects.filter(
        model_name=artifact.model_name, status=ModelArtifact.Status.DEPLOYED
    ).exclude(id=artifact.id).update(
        status=ModelArtifact.Status.RETIRED, retired_at=now
    )

    artifact.status = ModelArtifact.Status.DEPLOYED
    artifact.save(update_fields=["status"])
    return ModelDeployment.objects.create(
        model_artifact=artifact,
        trader=trader,
        status=ModelDeployment.Status.ACTIVE,
        deployed_at=now,
    )


def _artifact_dir() -> Path:
    """모델 파일 저장 디렉토리 (settings.MODEL_ARTIFACT_DIR 로 오버라이드 가능)."""
    default = Path(settings.BASE_DIR) / "artifacts"
    return Path(getattr(settings, "MODEL_ARTIFACT_DIR", default))


def load_predictor_from_artifact(artifact: ModelArtifact) -> LightGBMPredictor:
    """ModelArtifact.artifact_uri 에서 예측기를 로드한다."""
    return LightGBMPredictor.load(artifact.artifact_uri)


def train_from_dataset(
    dataset: TrainingDataset,
    model_name: str,
    version: str,
    artifact_dir: Optional[str] = None,
    params: Optional[dict] = None,
    deploy: bool = False,
) -> ModelArtifact:
    """
    데이터셋으로 모델을 학습하고 ModelArtifact 를 생성한다.

    deploy=True 면 status=DEPLOYED 로 저장하여 MLFilterEngine 이 즉시 사용한다.
    """
    items = list(dataset.items.all())
    if not items:
        raise ValueError("학습할 샘플이 없습니다.")

    feature_dicts = [item.feature_payload for item in items]
    labels = [item.label for item in items]

    predictor, metrics = LightGBMPredictor.train(feature_dicts, labels, params=params)

    directory = Path(artifact_dir) if artifact_dir else _artifact_dir()
    os.makedirs(directory, exist_ok=True)
    path = str(directory / f"{model_name}_{version}.pkl")
    checksum = predictor.save(path)

    metrics = {**metrics, "dataset": f"{dataset.name}:{dataset.version}"}
    return ModelArtifact.objects.create(
        model_name=model_name,
        version=version,
        artifact_uri=path,
        artifact_checksum=checksum,
        training_dataset_id=dataset.id,
        metrics_payload=metrics,
        status=ModelArtifact.Status.DEPLOYED if deploy else ModelArtifact.Status.READY,
        trained_at=timezone.now(),
    )
