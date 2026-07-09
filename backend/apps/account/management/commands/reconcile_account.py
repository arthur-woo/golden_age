"""
브로커 실보유 ↔ 내부 원장 리컨실리에이션 (안전#3).

예:
    python manage.py reconcile_account 1            # 대조만(리포트)
    python manage.py reconcile_account 1 --apply    # 브로커 기준으로 원장 보정
"""

from django.core.management.base import BaseCommand, CommandError

from apps.account.models import Account
from apps.account.services import get_broker_for_account
from core.pipeline.reconcile import reconcile_account


class Command(BaseCommand):
    help = "브로커 보유수량과 내부 원장을 대조/보정한다."

    def add_arguments(self, parser):
        parser.add_argument("account_id", type=int)
        parser.add_argument("--apply", action="store_true", help="차이를 원장에 보정")

    def handle(self, *args, **opts):
        try:
            account = Account.objects.get(id=opts["account_id"])
        except Account.DoesNotExist:
            raise CommandError(f"계좌를 찾을 수 없습니다: id={opts['account_id']}")

        broker = get_broker_for_account(account)
        result = reconcile_account(account, broker, apply=opts["apply"])

        mismatches = {s: d for s, d in result["positions"].items() if d["diff"] != 0}
        self.stdout.write(f"불일치 종목: {len(mismatches)}")
        for sym, d in mismatches.items():
            self.stdout.write(
                f"  {sym}: 내부 {d['internal']} / 브로커 {d['broker']} (diff {d['diff']})"
            )
        if opts["apply"]:
            self.stdout.write(self.style.SUCCESS(f"보정 원장 {result['adjusted']}건 생성"))
