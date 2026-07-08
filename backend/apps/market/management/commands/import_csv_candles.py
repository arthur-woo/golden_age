"""
CSV(일봉) 파일을 mkt_candle로 임포트한다(연구용 실데이터 확보, A-2).

기대 포맷: date,open,high,low,close,volume,Change  (파일명 = 종목코드)
예:
    python manage.py import_csv_candles --dir research/data/csv_data
"""

import csv
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.market.models import Candle
from apps.stock.models import Stock

KST = dt_timezone(timedelta(hours=9))
BULK_BATCH_SIZE = 1000


class Command(BaseCommand):
    help = "CSV 일봉 파일들을 mkt_candle로 멱등 적재한다."

    def add_arguments(self, parser):
        parser.add_argument("--dir", required=True, help="CSV 파일 디렉토리")
        parser.add_argument("--market", default=Stock.Market.KOSPI)
        parser.add_argument("--timeframe", default=Candle.Timeframe.DAY_1)
        parser.add_argument("--source", default="csv")

    def handle(self, *args, **opts):
        directory = Path(opts["dir"])
        if not directory.is_dir():
            raise CommandError(f"디렉토리를 찾을 수 없습니다: {directory}")

        files = sorted(directory.glob("*.csv"))
        if not files:
            raise CommandError(f"CSV 파일이 없습니다: {directory}")

        total_created = 0
        for path in files:
            symbol = path.stem
            stock, _ = Stock.objects.get_or_create(
                market=opts["market"], symbol=symbol, defaults={"name": symbol}
            )
            total_created += self._import_file(
                path, stock, opts["timeframe"], opts["source"]
            )

        self.stdout.write(
            self.style.SUCCESS(f"임포트 완료: {len(files)}종목, 신규 {total_created}봉")
        )

    def _import_file(
        self, path: Path, stock: Stock, timeframe: str, source: str
    ) -> int:
        candles = []
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    d = datetime.strptime(row["date"].strip(), "%Y-%m-%d")
                    candles.append(
                        Candle(
                            stock=stock,
                            timeframe=timeframe,
                            opened_at=d.replace(tzinfo=KST),
                            open_price=Decimal(row["open"]),
                            high_price=Decimal(row["high"]),
                            low_price=Decimal(row["low"]),
                            close_price=Decimal(row["close"]),
                            volume=Decimal(row["volume"]),
                            source=source,
                            raw_payload={"change": row.get("Change", "")},
                        )
                    )
                except (KeyError, ValueError, InvalidOperation):
                    continue  # 헤더 이상/결측 행은 건너뜀

        before = Candle.objects.filter(
            stock=stock, timeframe=timeframe, source=source
        ).count()
        Candle.objects.bulk_create(
            candles, batch_size=BULK_BATCH_SIZE, ignore_conflicts=True
        )
        after = Candle.objects.filter(
            stock=stock, timeframe=timeframe, source=source
        ).count()
        return after - before
