"""
수집 유니버스(또는 지정 종목)의 분봉을 REST로 일괄 백필한다 (수집#3).

예:
    python manage.py backfill_universe --account-id 1 --universe --pages 3
    python manage.py backfill_universe --account-id 1 --symbols 005930 000660
"""

from django.core.management.base import BaseCommand, CommandError

from apps.account.models import Account
from apps.stock.models import Stock, get_collection_stock_ids
from core.market.backfill import backfill_universe


class Command(BaseCommand):
    help = "여러 종목의 분봉을 KIS REST로 mkt_candle에 백필한다."

    def add_arguments(self, parser):
        parser.add_argument("--account-id", type=int, required=True)
        parser.add_argument("--universe", action="store_true", help="수집 유니버스 전체")
        parser.add_argument("--symbols", nargs="*", help="지정 종목 코드들")
        parser.add_argument("--market", default=Stock.Market.KOSPI)
        parser.add_argument("--pages", type=int, default=1)

    def handle(self, *args, **opts):
        try:
            account = Account.objects.get(id=opts["account_id"])
        except Account.DoesNotExist:
            raise CommandError(f"계좌를 찾을 수 없습니다: id={opts['account_id']}")

        if opts["universe"]:
            stocks = list(Stock.objects.filter(id__in=get_collection_stock_ids()))
        elif opts["symbols"]:
            stocks = list(
                Stock.objects.filter(market=opts["market"], symbol__in=opts["symbols"])
            )
        else:
            raise CommandError("--universe 또는 --symbols 중 하나가 필요합니다.")

        if not stocks:
            raise CommandError("백필할 종목이 없습니다.")

        result = backfill_universe(account, stocks, pages=opts["pages"])
        self.stdout.write(
            self.style.SUCCESS(f"백필 완료: {result['stocks']}종목 · 신규 {result['created']}봉")
        )
