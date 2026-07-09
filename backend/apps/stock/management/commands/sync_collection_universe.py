"""
상시 수집 유니버스(COLLECTION)를 구성/갱신한다 (수집#1).

수집 유니버스는 매매 유니버스와 분리된 '넓고 안정적인 상위집합'이며 append-only다.
지수 편출 종목도 계속 수집하여 데이터 구멍을 막는다.

예:
    python manage.py sync_collection_universe --all-with-candles
    python manage.py sync_collection_universe --symbols 005930 000660
"""

from django.core.management.base import BaseCommand, CommandError

from apps.market.models import Candle
from apps.stock.models import Stock, sync_collection_universe


class Command(BaseCommand):
    help = "수집 유니버스(COLLECTION)에 종목을 활성 편입한다(멱등)."

    def add_arguments(self, parser):
        parser.add_argument("--market", default=Stock.Market.KOSPI)
        parser.add_argument("--symbols", nargs="*", help="편입할 종목 코드들")
        parser.add_argument(
            "--all-with-candles",
            action="store_true",
            help="캔들이 있는 모든 종목을 편입",
        )

    def handle(self, *args, **opts):
        if opts["symbols"]:
            stocks = list(
                Stock.objects.filter(market=opts["market"], symbol__in=opts["symbols"])
            )
        elif opts["all_with_candles"]:
            stock_ids = Candle.objects.values_list("stock_id", flat=True).distinct()
            stocks = list(Stock.objects.filter(id__in=stock_ids))
        else:
            raise CommandError("--symbols 또는 --all-with-candles 중 하나가 필요합니다.")

        added = sync_collection_universe(stocks)
        from apps.stock.models import get_collection_stock_ids

        self.stdout.write(
            self.style.SUCCESS(
                f"수집 유니버스 갱신: 신규 {added}종목 · 현재 총 {len(get_collection_stock_ids())}종목"
            )
        )
