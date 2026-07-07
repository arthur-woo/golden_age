"""
학습 데이터셋 생성 -> LightGBM 학습 -> (선택) 배포까지 수행하는 운영 커맨드.

예:
    python manage.py train_daytrading_model --symbol 005930 --version v1 --deploy
"""

from django.core.management.base import BaseCommand, CommandError

from apps.stock.models import Stock
from apps.market.models import Candle
from core.ml.dataset import DatasetConfig, build_dataset_from_candles
from core.ml.training import deploy_artifact, train_from_dataset


class Command(BaseCommand):
    help = "1분봉 캔들로 학습 데이터셋을 만들고 LightGBM 모델을 학습/배포한다."

    def add_arguments(self, parser):
        parser.add_argument("--symbol", required=True, help="종목 코드")
        parser.add_argument("--market", default=Stock.Market.KOSPI)
        parser.add_argument("--model-name", default="lgb_signal_filter")
        # NOTE: --version 은 Django BaseCommand 예약어이므로 --model-version 사용
        parser.add_argument("--model-version", required=True, help="모델/데이터셋 버전")
        parser.add_argument("--timeframe", default=Candle.Timeframe.MIN_1)
        parser.add_argument("--upper", type=float, default=0.004)
        parser.add_argument("--lower", type=float, default=0.004)
        parser.add_argument("--horizon", type=int, default=10)
        parser.add_argument("--deploy", action="store_true", help="학습 후 즉시 배포")

    def handle(self, *args, **opts):
        try:
            stock = Stock.objects.get(market=opts["market"], symbol=opts["symbol"])
        except Stock.DoesNotExist:
            raise CommandError(f"종목을 찾을 수 없습니다: {opts['market']} {opts['symbol']}")

        config = DatasetConfig(
            upper=opts["upper"], lower=opts["lower"], horizon=opts["horizon"]
        )
        dataset = build_dataset_from_candles(
            stock,
            name=f"{opts['symbol']}_{opts['timeframe']}",
            version=opts["model_version"],
            timeframe=opts["timeframe"],
            config=config,
        )
        self.stdout.write(f"데이터셋 생성: {dataset} (샘플 {dataset.items.count()}개)")

        artifact = train_from_dataset(
            dataset, model_name=opts["model_name"], version=opts["model_version"]
        )
        self.stdout.write(f"학습 완료: {artifact} · metrics={artifact.metrics_payload}")

        if opts["deploy"]:
            deployment = deploy_artifact(artifact)
            self.stdout.write(self.style.SUCCESS(f"배포 완료: {deployment}"))
