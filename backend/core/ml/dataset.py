"""
학습 데이터셋 빌더 (Phase 5-2)

과거 1분봉 캔들로부터 오프라인 학습 데이터셋을 생성한다(콜드스타트).
각 진입 후보 시점에서
- 그 시점까지의 캔들로 Feature 스냅샷(mkt_feature_snapshot)을 만들고
- 이후 캔들로 Triple-Barrier 라벨(비용 차감)을 계산하여
- sys_training_dataset_item 으로 적재한다.

누설(look-ahead) 방지: Feature는 진입 시점 이전 정보만, 라벨은 이후 정보만 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.utils import timezone

from apps.market.models import Candle, FeatureSnapshot
from apps.stock.models import Stock
from apps.system.models import TrainingDataset, TrainingDatasetItem
from core.backtest.costs import CostConfig, round_trip_cost_ratio
from core.features.builder import build_features
from core.ml.labeling import triple_barrier_label

# 대량 삽입 배치 크기 (프로젝트 BULK-OPERATIONS 가이드)
BULK_BATCH_SIZE = 1000

# Feature 계산에 사용할 최대 lookback 봉 수
FEATURE_LOOKBACK = 100


@dataclass(frozen=True)
class DatasetConfig:
    """라벨/Feature 생성 파라미터."""

    upper: float = 0.004  # 익절 +0.4%
    lower: float = 0.004  # 손절 -0.4%
    horizon: int = 10  # 최대 보유 10봉
    min_history: int = 20  # Feature 계산 최소 이력 봉 수
    cost: Optional[float] = None  # None 이면 명시적 왕복비용률 사용


def build_dataset_from_candles(
    stock: Stock,
    name: str,
    version: str,
    timeframe: str = Candle.Timeframe.MIN_1,
    config: Optional[DatasetConfig] = None,
) -> TrainingDataset:
    """
    특정 종목의 과거 캔들로 학습 데이터셋을 생성하고 반환한다.

    Returns:
        status=READY 로 완료된 TrainingDataset. 샘플이 하나도 없으면 그대로 READY(빈 셋).
    """
    config = config or DatasetConfig()
    cost = config.cost
    if cost is None:
        cost = float(round_trip_cost_ratio(Decimal("0"), CostConfig()))

    candles_asc = list(
        Candle.objects.filter(stock=stock, timeframe=timeframe).order_by("opened_at")
    )
    closes = [float(c.close_price) for c in candles_asc]

    if not candles_asc:
        raise ValueError("데이터셋을 만들 캔들이 없습니다.")

    dataset = TrainingDataset.objects.create(
        name=name,
        version=version,
        source_started_at=candles_asc[0].opened_at,
        source_ended_at=candles_asc[-1].opened_at,
        feature_definition={
            "lookback": FEATURE_LOOKBACK,
            "min_history": config.min_history,
        },
        label_definition={
            "method": "triple_barrier",
            "upper": config.upper,
            "lower": config.lower,
            "horizon": config.horizon,
            "cost": cost,
        },
        status=TrainingDataset.Status.BUILDING,
    )

    # 1차: Feature 스냅샷과 라벨 계산 (진입 후보별)
    pending: list[tuple[FeatureSnapshot, "triple_barrier_label"]] = []
    snapshots: list[FeatureSnapshot] = []
    for i in range(len(candles_asc)):
        if i + 1 < config.min_history:
            continue  # Feature 이력 부족
        label = triple_barrier_label(
            closes,
            entry_index=i,
            upper=config.upper,
            lower=config.lower,
            horizon=config.horizon,
            cost=cost,
        )
        if label is None:
            continue  # 미래 데이터 부족

        window = list(reversed(candles_asc[: i + 1]))[:FEATURE_LOOKBACK]  # 최신 -> 과거
        feats = build_features(window, closes[i])
        fs = FeatureSnapshot(
            stock=stock,
            timeframe=timeframe,
            feature_payload=feats,
            source_payload={"dataset": name, "index": i},
            captured_at=candles_asc[i].opened_at,
        )
        snapshots.append(fs)
        pending.append((fs, label))

    # 2차: 스냅샷 대량 적재 (PK 획득)
    FeatureSnapshot.objects.bulk_create(snapshots, batch_size=BULK_BATCH_SIZE)

    # 3차: 학습 샘플 대량 적재
    items = [
        TrainingDatasetItem(
            training_dataset=dataset,
            feature_snapshot=fs,
            label=label.label,
            realized_return=Decimal(str(round(label.net_return, 6))),
            feature_payload=fs.feature_payload,
            label_payload={
                "barrier": label.barrier,
                "gross_return": round(label.gross_return, 6),
                "net_return": round(label.net_return, 6),
                "exit_index": label.exit_index,
            },
        )
        for fs, label in pending
    ]
    TrainingDatasetItem.objects.bulk_create(items, batch_size=BULK_BATCH_SIZE)

    dataset.status = TrainingDataset.Status.READY
    dataset.completed_at = timezone.now()
    dataset.save(update_fields=["status", "completed_at"])
    return dataset
