"""
Feature Engineering (Phase 5-2)

1분봉 캔들로부터 모델 입력 Feature Vector를 계산하는 순수 함수.
실거래 / 백테스트 / 학습 데이터 생성이 **이 함수 하나**를 공유하여
train-serve skew(학습-서빙 불일치)를 원천 차단한다.

규약
- 입력 캔들은 **최신 -> 과거** 순으로 전달된다(기존 StrategyRunner/trader_executor와 동일).
- 누설(look-ahead) 금지: 최신 캔들(index 0) 종가 시점까지의 정보만 사용.
- 출력은 JSON 직렬화 가능한 dict(float). 부족한 구간의 Feature는 생략한다.
- 횡단면(cross-sectional) 정규화는 유니버스 컨텍스트가 필요하므로 별도 단계에서 수행한다.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence


def _closes(candles: Sequence) -> list[float]:
    """최신 -> 과거 순 종가 리스트(float)."""
    return [float(c.close_price) for c in candles]


def _sma(values: list[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[:period]) / period


def _rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Wilder 단순 평균 RSI. closes는 최신 -> 과거 순."""
    if len(closes) < period + 1:
        return None
    history = list(reversed(closes[: period + 1]))  # 과거 -> 최신
    gains = 0.0
    losses = 0.0
    for i in range(len(history) - 1):
        change = history[i + 1] - history[i]
        if change > 0:
            gains += change
        else:
            losses += -change
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ret(closes: list[float], lag: int) -> Optional[float]:
    """최근 lag 봉 로그수익률 (최신 대비 lag봉 전)."""
    if len(closes) <= lag or closes[lag] <= 0:
        return None
    return math.log(closes[0] / closes[lag])


def _std(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)


def cross_sectional_rank(values: dict) -> dict:
    """
    같은 시점 유니버스 값들을 0~1 백분위 랭크로 변환한다(종목 간 스케일 제거).

    values: {symbol: value}. 반환: {symbol: rank01}. 원소 1개면 0.5.
    """
    items = [(k, v) for k, v in values.items() if v is not None]
    n = len(items)
    if n == 0:
        return {}
    if n == 1:
        return {items[0][0]: 0.5}
    ordered = sorted(items, key=lambda kv: kv[1])
    return {k: i / (n - 1) for i, (k, _) in enumerate(ordered)}


def build_features(
    candles: Sequence,
    current_price: Optional[float] = None,
    context: Optional[dict] = None,
) -> dict:
    """
    최신 -> 과거 순 캔들로부터 Feature dict를 생성한다.

    캔들 객체는 close_price, high_price, low_price, open_price, volume 속성을 가진
    apps.market.Candle(또는 동일 인터페이스)이면 된다.

    context(선택): 유니버스/지수/레짐 컨텍스트를 조인한다(설계 3.1 (7),(8)).
      - index_ret_1: 지수 1봉 로그수익 → 초과수익(잔차) 계산
      - beta: 종목 베타(기본 1.0)
      - index_vol, index_trend: 지수 변동성/추세
      - regime_code: 레짐 코드(BULL=1, SIDEWAYS=0, BEAR=-1 등)
      - cs_return_rank: 횡단면 수익률 랭크(0~1, 호출자가 cross_sectional_rank로 계산)
    """
    payload: dict = {}
    if not candles:
        return payload

    latest = candles[0]
    closes = _closes(candles)
    payload["close"] = closes[0]
    if current_price is not None:
        payload["current_price"] = float(current_price)

    # --- 모멘텀 / 추세 ---
    rsi = _rsi(closes, 14)
    if rsi is not None:
        payload["rsi"] = rsi

    sma5 = _sma(closes, 5)
    sma20 = _sma(closes, 20)
    if sma5 is not None:
        payload["sma_5"] = sma5
    if sma20 is not None:
        payload["sma_20"] = sma20
    if sma5 is not None and sma20 not in (None, 0):
        payload["sma_ratio"] = sma5 / sma20

    for lag in (1, 5, 15, 30):
        r = _ret(closes, lag)
        if r is not None:
            payload[f"ret_{lag}"] = r

    # --- 변동성 ---
    rets_1 = [
        math.log(closes[i] / closes[i + 1])
        for i in range(min(20, len(closes) - 1))
        if closes[i + 1] > 0
    ]
    if rets_1:
        payload["vol_std_20"] = _std(rets_1)

    # 최신 봉 범위/몸통 비율 (미시구조)
    high = float(latest.high_price)
    low = float(latest.low_price)
    open_ = float(latest.open_price)
    close0 = closes[0]
    if close0 > 0:
        payload["range_ratio"] = (high - low) / close0
    rng = high - low
    if rng > 0:
        payload["body_ratio"] = abs(close0 - open_) / rng

    # --- 거래량 ---
    volumes = [float(c.volume) for c in candles[:20]]
    if len(volumes) >= 2:
        vmean = sum(volumes) / len(volumes)
        vstd = _std(volumes)
        if vstd > 0:
            payload["volume_z"] = (volumes[0] - vmean) / vstd

    # --- VWAP 이격 (최근 20봉 근사 VWAP) ---
    window = candles[:20]
    tpv = sum(float(c.close_price) * float(c.volume) for c in window)
    vol_sum = sum(float(c.volume) for c in window)
    if vol_sum > 0:
        vwap = tpv / vol_sum
        if vwap > 0:
            payload["vwap_dev"] = (close0 - vwap) / vwap

    # --- 시간 Feature (opened_at 존재 시) ---
    opened_at = getattr(latest, "opened_at", None)
    if opened_at is not None:
        # 정규장 09:00 기준 경과분 (음수 방지 위해 max 0)
        minutes = opened_at.hour * 60 + opened_at.minute - (9 * 60)
        payload["minutes_since_open"] = float(max(minutes, 0))
        payload["dow"] = float(opened_at.weekday())

    # --- 횡단면·지수·레짐 컨텍스트 조인 ---
    if context:
        index_ret = context.get("index_ret_1")
        if index_ret is not None and "ret_1" in payload:
            beta = float(context.get("beta", 1.0))
            payload["excess_ret_1"] = payload["ret_1"] - beta * float(index_ret)
        for key in ("index_vol", "index_trend", "regime_code", "cs_return_rank"):
            if context.get(key) is not None:
                payload[key] = float(context[key])

    return payload
