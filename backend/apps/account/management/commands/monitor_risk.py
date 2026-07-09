"""
보유 포지션 손절/익절 상시 감시·청산 (안전#7).

분 파이프라인보다 자주(예: 10~30초) 스케줄해 급락 대응 지연을 줄인다.

예:
    python manage.py monitor_risk 1 --stop 0.05 --take 0.1
"""

from django.core.management.base import BaseCommand, CommandError

from apps.account.models import Account
from apps.account.services import get_broker_for_account
from core.pipeline.risk_monitor import monitor_and_exit


class Command(BaseCommand):
    help = "보유 포지션이 손절/익절선 이탈 시 즉시 보호 청산한다."

    def add_arguments(self, parser):
        parser.add_argument("account_id", type=int)
        parser.add_argument("--stop", type=float, default=0.05)
        parser.add_argument("--take", type=float, default=0.1)

    def handle(self, *args, **opts):
        try:
            account = Account.objects.get(id=opts["account_id"])
        except Account.DoesNotExist:
            raise CommandError(f"계좌를 찾을 수 없습니다: id={opts['account_id']}")

        broker = get_broker_for_account(account)
        exits = monitor_and_exit(account, broker, opts["stop"], opts["take"])
        self.stdout.write(self.style.SUCCESS(f"보호 청산 {exits}건"))
