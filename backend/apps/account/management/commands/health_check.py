"""
시스템 헬스체크 (안전#12).

데이터 신선도·미체결·마지막 실행 상태를 점검한다. 이상 시 비정상 종료코드(1) 반환.

예:
    python manage.py health_check 1
"""

import sys

from django.core.management.base import BaseCommand, CommandError

from apps.account.models import Account
from core.pipeline.health import system_health


class Command(BaseCommand):
    help = "계좌 기준 시스템 헬스를 점검한다(이상 시 exit 1)."

    def add_arguments(self, parser):
        parser.add_argument("account_id", type=int)
        parser.add_argument("--stale-minutes", type=int, default=5)

    def handle(self, *args, **opts):
        try:
            account = Account.objects.get(id=opts["account_id"])
        except Account.DoesNotExist:
            raise CommandError(f"계좌를 찾을 수 없습니다: id={opts['account_id']}")

        h = system_health(account, stale_minutes=opts["stale_minutes"])
        for k, v in h.items():
            self.stdout.write(f"  {k}: {v}")
        if h["healthy"]:
            self.stdout.write(self.style.SUCCESS("HEALTHY"))
        else:
            self.stdout.write(self.style.ERROR("UNHEALTHY — 개입 필요"))
            sys.exit(1)
