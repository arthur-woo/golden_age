"""
KIS REST로 당일 1분봉을 백필하여 mkt_candle에 적재하는 커맨드.

예:
    python manage.py backfill_candles --account-id 1 --symbol 005930 --pages 5
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from apps.account.models import Account
from apps.stock.models import Stock
from core.broker.kis.broker import KST
from core.market.aggregator import OHLCV
from core.market.ingest import persist_ohlcv


class Command(BaseCommand):
    help = "KIS 분봉 API로 mkt_candle을 백필한다(source=kis_rest)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--account-id", type=int, required=True, help="인증에 사용할 계좌 id"
        )
        parser.add_argument("--symbol", required=True)
        parser.add_argument("--market", default=Stock.Market.KOSPI)
        parser.add_argument("--pages", type=int, default=1, help="조회 반복 횟수(1회 약 30봉)")

    def handle(self, *args, **opts):
        from apps.account.services import get_broker_for_account

        try:
            account = Account.objects.get(id=opts["account_id"])
        except Account.DoesNotExist:
            raise CommandError(f"계좌를 찾을 수 없습니다: id={opts['account_id']}")
        try:
            stock = Stock.objects.get(market=opts["market"], symbol=opts["symbol"])
        except Stock.DoesNotExist:
            raise CommandError(f"종목을 찾을 수 없습니다: {opts['market']} {opts['symbol']}")

        broker = get_broker_for_account(account)

        to_time = ""
        earliest_seen = None
        created_total = 0
        for _ in range(opts["pages"]):
            candles = broker.get_minute_candles(opts["symbol"], to_time)
            if not candles:
                break
            for c in candles:
                _, created = persist_ohlcv(
                    stock,
                    OHLCV(
                        opened_at=c["opened_at"],
                        open=c["open"],
                        high=c["high"],
                        low=c["low"],
                        close=c["close"],
                        volume=c["volume"],
                        trade_count=0,
                    ),
                    source="kis_rest",
                )
                created_total += int(created)

            earliest = candles[0]["opened_at"]  # 오름차순 → [0]이 가장 과거
            if earliest == earliest_seen:
                break  # 더 이상 과거 데이터 없음
            earliest_seen = earliest
            to_time = (earliest.astimezone(KST) - timedelta(minutes=1)).strftime(
                "%H%M%S"
            )

        self.stdout.write(self.style.SUCCESS(f"백필 완료: 신규 {created_total}봉 적재"))
