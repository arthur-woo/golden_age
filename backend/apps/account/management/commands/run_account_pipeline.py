"""
계좌 단위 자동매매 파이프라인을 1회 실행하는 운영 커맨드.

분 단위 스케줄러(django_q)가 이 함수를 호출하도록 등록하거나, 수동 실행에 사용한다.

예:
    python manage.py run_account_pipeline 1
    python manage.py run_account_pipeline 1 --run-type MANUAL
"""

from django.core.management.base import BaseCommand

from apps.account.models import ExecutionRun
from core.pipeline.account_executor import execute_account_run


class Command(BaseCommand):
    help = "특정 계좌의 자동매매 파이프라인을 1회 실행한다."

    def add_arguments(self, parser):
        parser.add_argument("account_id", type=int)
        parser.add_argument(
            "--run-type",
            default=ExecutionRun.RunType.SCHEDULED,
            choices=[ExecutionRun.RunType.SCHEDULED, ExecutionRun.RunType.MANUAL],
        )

    def handle(self, *args, **opts):
        execute_account_run(opts["account_id"], run_type=opts["run_type"])
        self.stdout.write(self.style.SUCCESS(f"계좌 {opts['account_id']} 파이프라인 실행 완료"))
