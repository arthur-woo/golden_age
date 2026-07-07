"""
Walk-Forward Validation (Phase 5-4)

시계열 데이터에서 미래 누설 없이 재학습-검증을 시간순으로 반복한다.
모델 비의존 설계: train_fn / predict_fn / metric_fn 을 주입받아 어떤 모델(LightGBM 등)에도
동일하게 적용된다.

핵심 개념
- 시간 오름차순 정렬된 샘플을 전제로 한다.
- test 블록은 겹치지 않는 연속 구간이다.
- **embargo(=purge)**: train 종료와 test 시작 사이 gap. 라벨 시간창(horizon)만큼 두어
  겹치는 라벨로 인한 누설을 차단한다. 호출자가 embargo=horizon 으로 설정한다.
- anchored=True 면 train 시작을 0으로 고정(확장창), False 면 고정 길이 롤링창.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence


@dataclass(frozen=True)
class Fold:
    """단일 폴드의 인덱스 경계 (모두 [start, end) 반열림 구간)."""

    index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    @property
    def n_train(self) -> int:
        return self.train_end - self.train_start

    @property
    def n_test(self) -> int:
        return self.test_end - self.test_start

    def train_slice(self, seq: Sequence):
        return seq[self.train_start : self.train_end]

    def test_slice(self, seq: Sequence):
        return seq[self.test_start : self.test_end]


@dataclass
class FoldResult:
    fold: Fold
    metrics: dict


def generate_walk_forward_folds(
    n_samples: int,
    train_size: int,
    test_size: int,
    embargo: int = 0,
    anchored: bool = False,
) -> list[Fold]:
    """
    Walk-forward 폴드 목록을 생성한다.

    각 폴드의 train 은 test 시작보다 embargo 만큼 앞에서 종료된다(purge/embargo).
    """
    if train_size < 1 or test_size < 1:
        raise ValueError("train_size/test_size 는 1 이상이어야 합니다.")
    if embargo < 0:
        raise ValueError("embargo 는 0 이상이어야 합니다.")

    folds: list[Fold] = []
    test_start = train_size + embargo
    idx = 0
    while test_start + test_size <= n_samples:
        test_end = test_start + test_size
        train_end = test_start - embargo
        train_start = 0 if anchored else max(0, train_end - train_size)
        folds.append(
            Fold(
                index=idx,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        idx += 1
        test_start += test_size
    return folds


def run_walk_forward(
    X: Sequence,
    y: Sequence,
    folds: Sequence[Fold],
    train_fn: Callable,
    predict_fn: Callable,
    metric_fn: Callable[[Sequence, Sequence], dict],
) -> list[FoldResult]:
    """
    각 폴드에서 train_fn 으로 학습하고 test 구간을 예측하여 지표를 수집한다.

    Args:
        X, y: 시간 오름차순 정렬된 입력/타깃.
        train_fn(X_train, y_train) -> model
        predict_fn(model, X_test) -> preds
        metric_fn(y_test, preds) -> dict
    """
    if len(X) != len(y):
        raise ValueError("X 와 y 의 길이가 다릅니다.")

    results: list[FoldResult] = []
    for fold in folds:
        X_tr, y_tr = fold.train_slice(X), fold.train_slice(y)
        X_te, y_te = fold.test_slice(X), fold.test_slice(y)
        model = train_fn(X_tr, y_tr)
        preds = predict_fn(model, X_te)
        results.append(FoldResult(fold=fold, metrics=metric_fn(y_te, preds)))
    return results


def aggregate_fold_metrics(results: Sequence[FoldResult]) -> dict:
    """폴드별 지표의 평균과 표준편차를 집계한다(OOS 안정성 파악)."""
    if not results:
        return {}
    keys = results[0].metrics.keys()
    agg: dict = {}
    for key in keys:
        values = [r.metrics[key] for r in results if key in r.metrics]
        if not values:
            continue
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)
        agg[f"{key}_mean"] = mean
        agg[f"{key}_std"] = var**0.5
    agg["n_folds"] = len(results)
    return agg
