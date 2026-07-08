"""
데이터셋 Walk-Forward 학습 (B-8)

validation.py의 purge/embargo 분할과 표본 uniqueness 가중을 실제 LightGBM 학습에 결합한다.
겹치는 Triple-Barrier 라벨의 정보 중복을 uniqueness 가중으로 보정하고,
라벨 시간창(horizon)만큼 train/test 사이를 embargo하여 누설을 차단한다.
"""

from __future__ import annotations

from typing import Optional, Sequence

from apps.system.models import TrainingDataset
from core.ml.predictor import LightGBMPredictor, _auc
from core.ml.validation import generate_walk_forward_folds


def compute_uniqueness_weights(spans: Sequence[tuple]) -> list[float]:
    """
    라벨 구간 [entry, exit]들의 평균 uniqueness 가중을 계산한다(López de Prado).

    각 시점의 동시 라벨 수(concurrency)의 역수를 구간에서 평균한다.
    겹치지 않는 표본은 1.0, 많이 겹칠수록 작아진다.
    """
    if not spans:
        return []
    max_t = max(e for _, e in spans)
    concurrency = [0] * (max_t + 2)
    for start, end in spans:
        for t in range(start, end + 1):
            concurrency[t] += 1

    weights: list[float] = []
    for start, end in spans:
        vals = [
            1.0 / concurrency[t] for t in range(start, end + 1) if concurrency[t] > 0
        ]
        weights.append(sum(vals) / len(vals) if vals else 1.0)
    return weights


def _item_span(item) -> tuple:
    """학습 샘플의 라벨 구간 (entry_index, exit_index)을 복원한다."""
    entry = 0
    src = item.feature_snapshot.source_payload or {}
    if "index" in src:
        entry = int(src["index"])
    exit_index = entry
    lbl = item.label_payload or {}
    if "exit_index" in lbl:
        exit_index = int(lbl["exit_index"])
    return (entry, max(exit_index, entry))


def run_dataset_walk_forward(
    dataset: TrainingDataset,
    train_size: int,
    test_size: int,
    params: Optional[dict] = None,
) -> dict:
    """
    데이터셋을 시간순 정렬 후 purge/embargo + uniqueness 가중으로 폴드별 학습·평가한다.

    embargo는 라벨 horizon(label_definition)으로 설정하여 겹침 누설을 차단한다.
    반환: {"folds": [...per fold...], "auc_mean": ..., "auc_std": ..., "n_folds": ...}
    """
    items = list(
        dataset.items.select_related("feature_snapshot").order_by(
            "feature_snapshot__captured_at", "id"
        )
    )
    if not items:
        raise ValueError("학습 샘플이 없습니다.")

    X = [it.feature_payload for it in items]
    y = [it.label for it in items]
    spans = [_item_span(it) for it in items]
    weights = compute_uniqueness_weights(spans)

    horizon = int((dataset.label_definition or {}).get("horizon", 0))
    folds = generate_walk_forward_folds(
        len(items), train_size, test_size, embargo=horizon
    )

    fold_results = []
    aucs = []
    for fold in folds:
        X_tr = X[fold.train_start : fold.train_end]
        y_tr = y[fold.train_start : fold.train_end]
        w_tr = weights[fold.train_start : fold.train_end]
        X_te = X[fold.test_start : fold.test_end]
        y_te = y[fold.test_start : fold.test_end]

        predictor, _ = LightGBMPredictor.train(
            X_tr, y_tr, params=params, sample_weight=w_tr
        )
        preds = [predictor.predict_proba(x) for x in X_te]
        auc = _auc(y_te, preds)
        aucs.append(auc)
        fold_results.append(
            {
                "fold": fold.index,
                "n_train": fold.n_train,
                "n_test": fold.n_test,
                "auc": auc,
            }
        )

    mean = sum(aucs) / len(aucs) if aucs else 0.0
    var = sum((a - mean) ** 2 for a in aucs) / len(aucs) if aucs else 0.0
    return {
        "folds": fold_results,
        "auc_mean": mean,
        "auc_std": var**0.5,
        "n_folds": len(folds),
    }
