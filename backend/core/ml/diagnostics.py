"""
과최적화 진단 (B-9)

- Probabilistic Sharpe Ratio(PSR), Deflated Sharpe Ratio(DSR) — 다중시행·비정규성 보정 샤프 신뢰도
- Probability of Backtest Overfitting(PBO) — CSCV 기반 백테스트 과최적화 확률

Bailey & López de Prado. 값이 클수록(DSR) / 작을수록(PBO) 신뢰할 만하다.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
from scipy.stats import norm

EULER_GAMMA = 0.5772156649015329


def probabilistic_sharpe_ratio(
    sharpe: float,
    n_obs: int,
    sr_benchmark: float = 0.0,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """
    관측 샤프가 기준 샤프를 초과할 확률 PSR(0~1). sharpe/sr_benchmark는 봉당(비연율화) 값.
    """
    if n_obs < 2:
        return 0.5
    denom = math.sqrt(
        max(1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe**2, 1e-12)
    )
    stat = (sharpe - sr_benchmark) * math.sqrt(n_obs - 1) / denom
    return float(norm.cdf(stat))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """N회 시행 하 귀무가설(무스킬)에서의 기대 최대 샤프 SR0."""
    if n_trials < 2:
        return 0.0
    z1 = norm.ppf(1.0 - 1.0 / n_trials)
    z2 = norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(max(sr_variance, 0.0)) * (
        (1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2
    )


def deflated_sharpe_ratio(
    sharpe: float,
    n_obs: int,
    n_trials: int,
    sr_variance: float,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """
    다중시행(n_trials)과 시행 간 샤프 분산(sr_variance)을 반영한 Deflated Sharpe(0~1).
    """
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    return probabilistic_sharpe_ratio(
        sharpe, n_obs, sr_benchmark=sr0, skew=skew, kurtosis=kurtosis
    )


def pbo_cscv(performance: np.ndarray, n_splits: int = 8) -> float:
    """
    CSCV(Combinatorial Symmetric Cross-Validation)로 PBO를 추정한다.

    performance: (T 관측 x N 설정) 성과 행렬(봉별 수익 등). 반환: PBO(0~1).
    IS 최적 설정이 OS에서 중앙값 이하로 밀리는 조합의 비율.
    """
    M = np.asarray(performance, dtype=float)
    if M.ndim != 2 or M.shape[1] < 2:
        raise ValueError("performance는 (T, N>=2) 2차원 행렬이어야 합니다.")
    T, N = M.shape
    n_splits = min(n_splits, T)
    if n_splits < 2:
        raise ValueError("n_splits는 2 이상이어야 합니다.")

    blocks = np.array_split(np.arange(T), n_splits)
    half = n_splits // 2
    logits = []
    for is_blocks in itertools.combinations(range(n_splits), half):
        is_idx = np.concatenate([blocks[b] for b in is_blocks])
        os_idx = np.concatenate(
            [blocks[b] for b in range(n_splits) if b not in is_blocks]
        )
        is_perf = M[is_idx].sum(axis=0)
        os_perf = M[os_idx].sum(axis=0)

        best = int(np.argmax(is_perf))
        # OS 상대 순위(1..N) → (0,1)
        order = os_perf.argsort()
        ranks = np.empty(N)
        ranks[order] = np.arange(1, N + 1)
        w = ranks[best] / (N + 1)
        logits.append(math.log(w / (1.0 - w)))

    return float(np.mean([1.0 if l <= 0 else 0.0 for l in logits]))
