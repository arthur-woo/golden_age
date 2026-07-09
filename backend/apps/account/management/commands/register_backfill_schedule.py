"""
수집 유니버스 분봉 백필을 매 N분 자동 실행하도록 django_q 스케줄을 등록한다 (수집#3).

예:
    python manage.py register_backfill_schedule --account-id 1 --minutes 5 --pages 2
"""

from django.core.management.base import BaseCommand, CommandError

from apps.account.models import Account


class Command(BaseCommand):
    help = "backfill_universe를 매 N분 실행하는 django_q 스케줄을 등록(멱등)한다."

    def add_arguments(self, parser):
        parser.add_argument("--account-id", type=int, required=True)
        parser.add_argument("--minutes", type=int, default=5)
        parser.add_argument("--pages", type=int, default=1)
        parser.add_argument("--name", default=None)

    def handle(self, *args, **opts):
        from django_q.models import Schedule
        from django_q.tasks import schedule

        account_id = opts["account_id"]
        if not Account.objects.filter(id=account_id).exists():
            raise CommandError(f"계좌를 찾을 수 없습니다: id={account_id}")

        name = opts["name"] or f"backfill-universe-{account_id}"
        Schedule.objects.filter(name=name).delete()  # 멱등
        schedule(
            "django.core.management.call_command",
            "backfill_universe",
            account_id=account_id,
            universe=True,
            pages=opts["pages"],
            name=name,
            schedule_type=Schedule.MINUTES,
            minutes=opts["minutes"],
            repeats=-1,
        )
        self.stdout.write(
            self.style.SUCCESS(f"백필 스케줄 등록: {name} (매 {opts['minutes']}분)")
        )
