"""
KIS 실시간(WebSocket) 체결 어댑터 (잔여 B)

한국투자증권 실시간 체결가(tr_id=H0STCNT0) 스트림을 파싱하여 RealtimeCollector에 주입한다.
소켓 I/O는 계정 인증/네트워크가 필요하므로, 파싱과 수집 연결(테스트 가능)과
실제 접속(문서화된 연결 지점)을 분리한다.

프레임 형식(비암호화):
    "0|H0STCNT0|001|<데이터>"
    - parts[0]: 암호화 유무('0' 평문, '1' 암호화)
    - parts[1]: tr_id
    - parts[2]: 데이터 건수
    - parts[3]: '^'로 구분된 필드들(건수 × 레코드당 필드수)

H0STCNT0 레코드 필드 인덱스(발췌): 0=종목코드, 1=체결시간(HHMMSS), 2=현재가, 12=체결거래량
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Iterable, Optional

from apps.account.models import Account
from apps.stock.models import Stock
from core.market.ingest import RealtimeCollector

logger = logging.getLogger(__name__)

REALTIME_TRADE_TR_ID = "H0STCNT0"  # 실시간 주식체결가
FIELDS_PER_RECORD = 46  # H0STCNT0 레코드당 필드 수
KST = dt_timezone(timedelta(hours=9))  # 한국 표준시


@dataclass(frozen=True)
class TickData:
    symbol: str
    ts: datetime
    price: Decimal
    volume: Decimal


def _parse_hhmmss(hhmmss: str, trade_date: Optional[date]) -> datetime:
    trade_date = trade_date or datetime.now(KST).date()
    h, m, s = int(hhmmss[0:2]), int(hhmmss[2:4]), int(hhmmss[4:6])
    return datetime(
        trade_date.year, trade_date.month, trade_date.day, h, m, s, tzinfo=KST
    )


def parse_realtime_message(
    raw: str, trade_date: Optional[date] = None
) -> list[TickData]:
    """
    실시간 프레임을 파싱하여 체결 틱 리스트를 반환한다.

    체결가(H0STCNT0)가 아니면 빈 리스트, 암호화 프레임이면 ValueError.
    """
    parts = raw.split("|")
    if len(parts) < 4:
        return []
    encrypted, tr_id, count_s, body = parts[0], parts[1], parts[2], parts[3]
    if tr_id != REALTIME_TRADE_TR_ID:
        return []
    if encrypted == "1":
        raise ValueError("암호화된 실시간 데이터는 지원하지 않습니다.")

    try:
        count = int(count_s)
    except ValueError:
        count = 1

    fields = body.split("^")
    ticks: list[TickData] = []
    for i in range(count):
        base = i * FIELDS_PER_RECORD
        rec = fields[base : base + FIELDS_PER_RECORD]
        if len(rec) < 13:
            break
        ticks.append(
            TickData(
                symbol=rec[0],
                ts=_parse_hhmmss(rec[1], trade_date),
                price=Decimal(rec[2]),
                volume=Decimal(rec[12]),
            )
        )
    return ticks


class KISRealtimeAdapter:
    """KIS 실시간 체결 → RealtimeCollector 연결 어댑터."""

    LIVE_WS_URL = "ws://ops.koreainvestment.com:21000"
    PAPER_WS_URL = "ws://ops.koreainvestment.com:31000"

    def __init__(
        self,
        account: Account,
        collector: Optional[RealtimeCollector] = None,
        trade_date: Optional[date] = None,
    ):
        self.account = account
        self.collector = collector or RealtimeCollector(source="kis_ws")
        self.trade_date = trade_date
        self._symbol_to_stock_id: dict[str, Optional[int]] = {}

    def _stock_id(self, symbol: str) -> Optional[int]:
        if symbol not in self._symbol_to_stock_id:
            stock = Stock.objects.filter(symbol=symbol).first()
            self._symbol_to_stock_id[symbol] = stock.id if stock else None
        return self._symbol_to_stock_id[symbol]

    def ticks_from_messages(self, message_source: Iterable[str]):
        """원시 메시지 스트림을 (stock_id, ts, price, volume) 틱 스트림으로 변환한다."""
        for raw in message_source:
            for tick in parse_realtime_message(raw, self.trade_date):
                stock_id = self._stock_id(tick.symbol)
                if stock_id is None:
                    logger.debug("미등록 종목 틱 무시: %s", tick.symbol)
                    continue
                yield (stock_id, tick.ts, tick.price, tick.volume)

    def run(self, message_source: Iterable[str], finalize: bool = True) -> int:
        """
        메시지 소스를 소비하여 완성된 1분봉을 적재한다. 적재 캔들 수를 반환한다.

        message_source는 테스트/리플레이/실 WebSocket 어댑터 모두 가능(순수 iterable).
        """
        return self.collector.consume(
            self.ticks_from_messages(message_source), finalize=finalize
        )

    # --- 실 접속(문서화된 연결 지점, 네트워크/인증 필요) ---
    def connect_and_run(self, symbols: list[str]):  # pragma: no cover
        """
        실 WebSocket에 접속하여 symbols를 구독하고 run()으로 수집한다.

        websocket-client 등 WS 라이브러리와 approval_key(REST /oauth2/Approval)가 필요하다.
        운영 배포 시 구현한다.
        """
        raise NotImplementedError(
            "실 WebSocket 접속은 WS 라이브러리와 approval_key 발급 연결이 필요합니다. "
            "테스트/리플레이에는 run(message_source)를 사용하세요."
        )
