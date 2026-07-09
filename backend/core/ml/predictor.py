"""
LightGBM Predictor (Phase 5-5)

Feature dict(build_features 산출물) 기반 이진분류 모델의 학습/추론/직렬화를 담당한다.
매매 성공 확률 P(익절|비용차감)을 예측한다.

아티팩트는 booster 문자열 + feature 순서를 함께 pickle 하여 단일 파일로 저장한다
(추론 시 feature 정렬 재현을 보장).
"""

from __future__ import annotations

import hashlib
import pickle
from dataclasses import dataclass
from typing import Optional, Sequence

import lightgbm as lgb
import numpy as np

DEFAULT_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "num_leaves": 15,
    "min_data_in_leaf": 5,
    "learning_rate": 0.05,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.9,
    "bagging_freq": 1,
    "lambda_l1": 0.0,
    "lambda_l2": 0.0,
    "verbosity": -1,
}
DEFAULT_NUM_ROUNDS = 50


def _auc(y: Sequence[int], p: Sequence[float]) -> float:
    """Mann–Whitney U 기반 AUC (외부 의존성 없이 소규모 데이터용)."""
    pos = [pi for yi, pi in zip(y, p) if yi == 1]
    neg = [pi for yi, pi in zip(y, p) if yi == 0]
    if not pos or not neg:
        return 0.5
    wins = 0.0
    for a in pos:
        for b in neg:
            wins += 1.0 if a > b else (0.5 if a == b else 0.0)
    return wins / (len(pos) * len(neg))


def _vectorize(
    feature_dicts: Sequence[dict], feature_names: Sequence[str]
) -> np.ndarray:
    """feature dict 리스트를 feature_names 순서의 행렬로 변환(결측 NaN)."""
    matrix = np.full((len(feature_dicts), len(feature_names)), np.nan, dtype=float)
    for i, d in enumerate(feature_dicts):
        for j, name in enumerate(feature_names):
            v = d.get(name)
            if v is not None:
                matrix[i, j] = float(v)
    return matrix


@dataclass
class LightGBMPredictor:
    booster: lgb.Booster
    feature_names: list[str]
    calibrator: object = None  # core.ml.calibration.* (선택)

    @classmethod
    def train(
        cls,
        feature_dicts: Sequence[dict],
        labels: Sequence[int],
        params: Optional[dict] = None,
        num_boost_round: int = DEFAULT_NUM_ROUNDS,
        sample_weight: Optional[Sequence[float]] = None,
    ) -> tuple["LightGBMPredictor", dict]:
        """
        Feature dict + 라벨로 학습하고 (predictor, metrics) 를 반환한다.

        feature_names 는 전체 dict 키의 정렬된 합집합으로 고정한다.
        sample_weight: 표본 가중치(겹침 라벨 uniqueness 등). None이면 균등.
        """
        if len(feature_dicts) != len(labels):
            raise ValueError("feature 수와 label 수가 다릅니다.")
        if not feature_dicts:
            raise ValueError("학습 데이터가 비어 있습니다.")

        feature_names = sorted({k for d in feature_dicts for k in d.keys()})
        X = _vectorize(feature_dicts, feature_names)
        y = np.asarray(labels, dtype=int)
        weight = (
            np.asarray(sample_weight, dtype=float)
            if sample_weight is not None
            else None
        )

        train_set = lgb.Dataset(
            X, label=y, weight=weight, feature_name=list(feature_names)
        )
        booster = lgb.train(
            params or DEFAULT_PARAMS,
            train_set,
            num_boost_round=num_boost_round,
        )
        predictor = cls(booster=booster, feature_names=feature_names)

        train_preds = booster.predict(X)
        preds_label = (train_preds >= 0.5).astype(int)
        metrics = {
            "n_samples": int(len(labels)),
            "n_features": int(len(feature_names)),
            "positive_rate": float(y.mean()) if len(y) else 0.0,
            "train_accuracy": float((preds_label == y).mean()),
            "train_auc": _auc(y.tolist(), train_preds.tolist()),
        }
        return predictor, metrics

    def predict_proba(self, feature_dict: dict) -> float:
        """단일 Feature dict에 대한 양성(매매 성공) 확률. 보정기가 있으면 적용."""
        X = _vectorize([feature_dict], self.feature_names)
        raw = float(self.booster.predict(X)[0])
        if self.calibrator is not None:
            return self.calibrator.predict(raw)
        return raw

    def fit_calibration(
        self,
        feature_dicts: Sequence[dict],
        labels: Sequence[int],
        method: str = "isotonic",
    ):
        """검증셋으로 확률 보정기를 학습해 부착한다(isotonic|platt)."""
        from core.ml.calibration import fit_calibrator

        raws = self.booster.predict(_vectorize(feature_dicts, self.feature_names))
        self.calibrator = fit_calibrator(list(map(float, raws)), list(labels), method)
        return self.calibrator

    def save(self, path: str) -> str:
        """모델을 path 에 저장하고 파일 checksum(sha256)을 반환한다."""
        blob = {
            "model_str": self.booster.model_to_string(),
            "feature_names": self.feature_names,
            "calibrator": self.calibrator.to_dict() if self.calibrator else None,
        }
        data = pickle.dumps(blob)
        with open(path, "wb") as f:
            f.write(data)
        return hashlib.sha256(data).hexdigest()

    @classmethod
    def load(cls, path: str) -> "LightGBMPredictor":
        from core.ml.calibration import calibrator_from_dict

        with open(path, "rb") as f:
            blob = pickle.loads(f.read())
        booster = lgb.Booster(model_str=blob["model_str"])
        return cls(
            booster=booster,
            feature_names=blob["feature_names"],
            calibrator=calibrator_from_dict(blob.get("calibrator")),
        )
