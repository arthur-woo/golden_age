"""
실시간 1분봉 수집 러너 (연결 #1)

운영: KIS WebSocket 체결 스트림을 RealtimeCollector에 주입하여 mkt_candle에 적재한다.
      (WebSocket 어댑터는 계정 인증/네트워크가 필요하여 별도 연결 지점으로 둔다.)

검증/스모크: --demo 로 합성 틱을 생성해 수집→적재 파이프라인을 실제 DB에 적용해 본다.

예:
    python manage.py collect_realtime --symbol 005930 --demo
"""

from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.stock.models import Stock
from core.market.aggregator import floor_to_minute
from core.market.ingest import RealtimeCollector


def _demo_ticks(stock_id: int, minutes: int):
    """직전 `minutes`분 동안의 합성 틱을 생성한다(완성된 분들)."""
    start = floor_to_minute(timezone.now()) - timedelta(minutes=minutes)
    price = Decimal("70000")
    for m in range(minutes):
        minute_start = start + timedelta(minutes=m)
        for s in (5, 20, 40, 55):
            price += Decimal("50") if s % 2 == 0 else Decimal("-30")
            yield (stock_id, minute_start + timedelta(seconds=s), price, Decimal("100"))


class Command(BaseCommand):
    help = "실시간 체결을 1분봉으로 집계해 mkt_candle에 적재한다."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", required=True)
        parser.add_argument("--market", default=Stock.Market.KOSPI)
        parser.add_argument("--source", default="kis_ws")
        parser.add_argument("--demo", action="store_true", help="합성 틱으로 파이프라인 검증")
        parser.add_argument("--demo-minutes", type=int, default=3)
        parser.add_argument("--account-id", type=int, help="실시간 모드 인증에 사용할 계좌 id")

    def handle(self, *args, **opts):
        try:
            stock = Stock.objects.get(market=opts["market"], symbol=opts["symbol"])
        except Stock.DoesNotExist:
            raise CommandError(f"종목을 찾을 수 없습니다: {opts['market']} {opts['symbol']}")

        collector = RealtimeCollector(source=opts["source"])

        if opts["demo"]:
            total = collector.consume(
                _demo_ticks(stock.id, opts["demo_minutes"]), finalize=True
            )
            self.stdout.write(self.style.SUCCESS(f"합성 수집 완료: {total}개 캔들 적재"))
            return

        if not opts.get("account_id"):
            raise CommandError("실시간 모드에는 --account-id 가 필요합니다. (검증은 --demo 사용)")
        from apps.account.models import Account
        from core.broker.kis.realtime import KISRealtimeAdapter

        try:
            account = Account.objects.get(id=opts["account_id"])
        except Account.DoesNotExist:
            raise CommandError(f"계좌를 찾을 수 없습니다: id={opts['account_id']}")

        adapter = KISRealtimeAdapter(account, collector=collector)
        self.stdout.write("KIS 실시간 접속 시작... (Ctrl+C로 종료)")
        adapter.connect_and_run([opts["symbol"]])
