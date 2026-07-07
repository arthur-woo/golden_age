"""
Triple-Barrier 라벨링 (Phase 5-2)

López de Prado의 삼중 배리어 기법으로 진입 후보에 라벨을 부여하는 순수 함수.
long-only 초단기 매매를 가정한다(진입=매수, 청산=매도).

세 배리어
- 익절(upper): 진입가 대비 +upper 수익률
- 손절(lower): 진입가 대비 -lower 수익률
- 시간(horizon): 최대 보유 봉 수. 배리어 미도달 시 horizon 봉에서 청산.

라벨은 **비용 차감 후** 순수익 부호로 정한다(비용을 이기는 신호만 positive).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

UPPER = "UPPER"
LOWER = "LOWER"
TIME = "TIME"


@dataclass(frozen=True)
class BarrierLabel:
    """삼중 배리어 라벨 결과."""

    barrier: str  # UPPER / LOWER / TIME
    exit_index: int  # 청산이 일어난 캔들 인덱스
    gross_return: float  # 비용 차감 전 수익률
    net_return: float  # 비용 차감 후 수익률
    label: int  # 1 = 순수익 > 0, 0 = 그 외 (이진 분류 타깃)


def triple_barrier_label(
    closes: Sequence[float],
    entry_index: int,
    upper: float,
    lower: float,
    horizon: int,
    cost: float = 0.0,
) -> Optional[BarrierLabel]:
    """
    과거 -> 최신 순 종가 시퀀스에서 entry_index 진입에 대한 라벨을 계산한다.

    Args:
        closes: 시간 오름차순(과거 -> 최신) 종가.
        entry_index: 진입 캔들 인덱스. 체결은 entry_index+1부터 관찰.
        upper: 익절 수익률 임계(양수, 예: 0.004 = +0.4%).
        lower: 손절 수익률 임계(양수, 예: 0.004 = -0.4%).
        horizon: 최대 보유 봉 수(>=1).
        cost: 왕복 거래비용률(예: 0.0021). net_return = gross - cost.

    Returns:
        BarrierLabel. 미래 데이터가 부족하면 None(라벨링 불가).
    """
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    n = len(closes)
    entry_price = closes[entry_index]
    if entry_price <= 0:
        return None
    # 최소 1봉 이후는 관찰 가능해야 함
    if entry_index + 1 >= n:
        return None

    last_index = min(entry_index + horizon, n - 1)
    exit_index = last_index
    barrier = TIME
    gross_return = closes[last_index] / entry_price - 1.0

    for i in range(entry_index + 1, last_index + 1):
        ret = closes[i] / entry_price - 1.0
        if ret >= upper:
            barrier = UPPER
            exit_index = i
            gross_return = ret
            break
        if ret <= -lower:
            barrier = LOWER
            exit_index = i
            gross_return = ret
            break

    net_return = gross_return - cost
    return BarrierLabel(
        barrier=barrier,
        exit_index=exit_index,
        gross_return=gross_return,
        net_return=net_return,
        label=1 if net_return > 0 else 0,
    )
