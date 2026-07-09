"""
확률 보정 (B-6)

모델 출력 점수를 실제 성공 확률로 보정한다. 사이징·임계치가 확률에 의존하므로 필수(설계 5.2).
- IsotonicCalibrator: 단조 비모수 보정(PAV, 외부 의존성 없음)
- PlattCalibrator: 로지스틱 보정 sigmoid(a*s+b) (scipy 최적화)

각 보정기는 to_dict/from_dict로 직렬화되어 아티팩트에 함께 저장된다.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from scipy.optimize import minimize


def _pav(values: Sequence[float]) -> list[float]:
    """Pool Adjacent Violators — 단조 비감소 적합값을 반환한다."""
    # blocks: [sum, count]
    blocks: list[list[float]] = []
    for v in values:
        blocks.append([float(v), 1.0])
        while len(blocks) >= 2 and (blocks[-2][0] / blocks[-2][1]) >= (
            blocks[-1][0] / blocks[-1][1]
        ):
            s2, c2 = blocks.pop()
            s1, c1 = blocks.pop()
            blocks.append([s1 + s2, c1 + c2])
    out: list[float] = []
    for s, c in blocks:
        out.extend([s / c] * int(c))
    return out


class IsotonicCalibrator:
    method = "isotonic"

    def __init__(self, xs: list[float], ys: list[float]):
        self.xs = xs
        self.ys = ys

    @classmethod
    def fit(
        cls, scores: Sequence[float], labels: Sequence[int]
    ) -> "IsotonicCalibrator":
        order = np.argsort(np.asarray(scores, dtype=float))
        sorted_scores = [float(scores[i]) for i in order]
        sorted_labels = [float(labels[i]) for i in order]
        fitted = _pav(sorted_labels)
        # 중복 점수는 마지막 적합값으로 정리(단조 유지)
        xs: list[float] = []
        ys: list[float] = []
        for s, y in zip(sorted_scores, fitted):
            if xs and xs[-1] == s:
                ys[-1] = y
            else:
                xs.append(s)
                ys.append(y)
        return cls(xs, ys)

    def predict(self, score: float) -> float:
        if not self.xs:
            return float(score)
        return float(min(1.0, max(0.0, np.interp(score, self.xs, self.ys))))

    def to_dict(self) -> dict:
        return {"method": self.method, "xs": self.xs, "ys": self.ys}

    @classmethod
    def from_dict(cls, d: dict) -> "IsotonicCalibrator":
        return cls(list(d["xs"]), list(d["ys"]))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


class PlattCalibrator:
    method = "platt"

    def __init__(self, a: float, b: float):
        self.a = a
        self.b = b

    @classmethod
    def fit(cls, scores: Sequence[float], labels: Sequence[int]) -> "PlattCalibrator":
        s = np.asarray(scores, dtype=float)
        y = np.asarray(labels, dtype=float)

        def nll(params):
            a, b = params
            z = a * s + b
            p = 1.0 / (1.0 + np.exp(-z))
            p = np.clip(p, 1e-12, 1 - 1e-12)
            return -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))

        res = minimize(nll, x0=np.array([1.0, 0.0]), method="Nelder-Mead")
        a, b = float(res.x[0]), float(res.x[1])
        return cls(a, b)

    def predict(self, score: float) -> float:
        return float(min(1.0, max(0.0, _sigmoid(self.a * float(score) + self.b))))

    def to_dict(self) -> dict:
        return {"method": self.method, "a": self.a, "b": self.b}

    @classmethod
    def from_dict(cls, d: dict) -> "PlattCalibrator":
        return cls(float(d["a"]), float(d["b"]))


def calibrator_from_dict(d: dict):
    """직렬화 dict로부터 보정기를 복원한다."""
    if not d:
        return None
    if d.get("method") == "isotonic":
        return IsotonicCalibrator.from_dict(d)
    if d.get("method") == "platt":
        return PlattCalibrator.from_dict(d)
    return None


def fit_calibrator(scores, labels, method: str = "isotonic"):
    """method('isotonic'|'platt')에 맞는 보정기를 학습해 반환한다."""
    if method == "platt":
        return PlattCalibrator.fit(scores, labels)
    return IsotonicCalibrator.fit(scores, labels)
