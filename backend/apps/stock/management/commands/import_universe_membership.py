"""
지수 유니버스(예: KOSPI200) 구성종목을 리밸런싱 반영한다 (안전#10).

목록에 빠진 종목은 편출, 새로 든 종목은 편입(point-in-time 이력 유지).

예:
    python manage.py import_universe_membership --universe KOSPI200 --file kospi200.txt
    python manage.py import_universe_membership --universe KOSPI200 --symbols 005930 000660
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.stock.models import (
    Stock,
    UniverseMembership,
    get_universe_stock_ids,
    rebalance_universe,
)


class Command(BaseCommand):
    help = "지수 유니버스 구성종목을 리밸런싱한다(편입/편출 이력 유지)."

    def add_arguments(self, parser):
        parser.add_argument("--universe", default=UniverseMembership.Universe.KOSPI200)
        parser.add_argument("--market", default=Stock.Market.KOSPI)
        parser.add_argument("--symbols", nargs="*")
        parser.add_argument("--file", help="종목코드 줄바꿈 목록 파일")

    def handle(self, *args, **opts):
        symbols = list(opts["symbols"] or [])
        if opts["file"]:
            path = Path(opts["file"])
            if not path.is_file():
                raise CommandError(f"파일을 찾을 수 없습니다: {path}")
            symbols += [
                ln.strip() for ln in path.read_text().splitlines() if ln.strip()
            ]
        if not symbols:
            raise CommandError("--symbols 또는 --file 이 필요합니다.")

        stocks = list(
            Stock.objects.filter(market=opts["market"], symbol__in=set(symbols))
        )
        result = rebalance_universe(opts["universe"], stocks)
        total = len(get_universe_stock_ids(opts["universe"]))
        self.stdout.write(
            self.style.SUCCESS(
                f"{opts['universe']} 리밸런싱: 편입 {result['added']} / 편출 {result['removed']} · 현재 {total}종목"
            )
        )
