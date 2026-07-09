"""
지수 레짐 판단 (B-5 · 설계 1.3 2차)

지수(또는 대표 ETF) 캔들의 추세·변동성 2축 Feature를 KMeans로 군집화하여 레짐을 추정한다.
가장 변동성이 큰 군집을 CHAOS로 보고 신규 진입 차단(kill-switch) 파라미터를 산출한다.
scipy.cluster.vq.kmeans2를 사용하므로 별도 의존성이 없다.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.cluster.vq import kmeans2

# 추세 판정 임계(레짐 라벨링용)
TREND_EPS = 0.005


def _std(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def build_index_features(closes: list[float], window: int = 20):
    """
    시간 오름차순 종가로부터 (추세, 변동성) Feature와 원 인덱스를 만든다.

    trend = window봉 수익률, vol = window봉 1봉수익률 표준편차.
    """
    feats: list[list[float]] = []
    indices: list[int] = []
    for i in range(window, len(closes)):
        base = closes[i - window]
        trend = (closes[i] / base - 1.0) if base > 0 else 0.0
        rets = [
            closes[j] / closes[j - 1] - 1.0
            for j in range(i - window + 1, i + 1)
            if closes[j - 1] > 0
        ]
        feats.append([trend, _std(rets)])
        indices.append(i)
    return feats, indices


class IndexRegimeModel:
    """추세×변동성 KMeans 레짐 모델."""

    def __init__(self, centroids, mean, std, chaos_label, trend_by_label):
        self.centroids = centroids
        self.mean = mean
        self.std = std
        self.chaos_label = chaos_label
        self.trend_by_label = trend_by_label

    @classmethod
    def fit(
        cls, feature_matrix, n_regimes: int = 4, seed: int = 0
    ) -> "IndexRegimeModel":
        X = np.asarray(feature_matrix, dtype=float)
        if X.ndim != 2 or len(X) < n_regimes:
            raise ValueError("feature_matrix가 부족합니다(행 수 >= n_regimes).")
        mean = X.mean(axis=0)
        std = X.std(axis=0)
        std[std == 0] = 1.0
        Z = (X - mean) / std
        centroids, labels = kmeans2(Z, n_regimes, seed=seed, minit="++")

        trend_by_label = {}
        vol_by_label = {}
        for k in range(n_regimes):
            mask = labels == k
            trend_by_label[k] = float(X[mask, 0].mean()) if mask.any() else 0.0
            vol_by_label[k] = float(X[mask, 1].mean()) if mask.any() else 0.0
        chaos_label = max(vol_by_label, key=vol_by_label.get)
        return cls(centroids, mean, std, chaos_label, trend_by_label)

    def predict_label(self, feature) -> int:
        z = (np.asarray(feature, dtype=float) - self.mean) / self.std
        dist = ((self.centroids - z) ** 2).sum(axis=1)
        return int(dist.argmin())

    def regime_info(self, feature) -> dict:
        """
        레짐/코드/CHAOS 여부와 하위 트레이더 전달용 parameter_payload를 반환한다.
        parameter_payload는 mkt_regime_snapshot에 그대로 저장 가능.
        """
        k = self.predict_label(feature)
        trend = self.trend_by_label[k]
        is_chaos = k == self.chaos_label

        if trend > TREND_EPS:
            regime, code = "BULL", 1.0
        elif trend < -TREND_EPS:
            regime, code = "BEAR", -1.0
        else:
            regime, code = "SIDEWAYS", 0.0

        payload: dict = {}
        if is_chaos:
            payload["block_new_entries"] = True
            payload["position_size_multiplier"] = "0.5"

        return {
            "regime": regime,
            "regime_code": code,
            "is_chaos": is_chaos,
            "cluster": k,
            "parameter_payload": payload,
        }
