"""
계좌 파이프라인을 매 N분 실행하도록 django_q 스케줄을 등록한다 (C-12).

예:
    python manage.py register_schedule --account-id 1 --minutes 1
장중에만 돌리려면 운영에서 cron 표현식/워커 가동 시간대를 별도 관리한다.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.account.models import Account


class Command(BaseCommand):
    help = "run_account_pipeline을 매 N분 실행하는 django_q 스케줄을 등록(멱등)한다."

    def add_arguments(self, parser):
        parser.add_argument("--account-id", type=int, required=True)
        parser.add_argument("--minutes", type=int, default=1)
        parser.add_argument("--name", default=None)

    def handle(self, *args, **opts):
        from django_q.models import Schedule
        from django_q.tasks import schedule

        account_id = opts["account_id"]
        if not Account.objects.filter(id=account_id).exists():
            raise CommandError(f"계좌를 찾을 수 없습니다: id={account_id}")

        name = opts["name"] or f"daytrading-account-{account_id}"
        # 멱등: 동일 이름 스케줄은 재등록
        Schedule.objects.filter(name=name).delete()
        schedule(
            "django.core.management.call_command",
            "run_account_pipeline",
            str(account_id),
            name=name,
            schedule_type=Schedule.MINUTES,
            minutes=opts["minutes"],
            repeats=-1,
        )
        self.stdout.write(self.style.SUCCESS(f"스케줄 등록: {name} (매 {opts['minutes']}분)"))
