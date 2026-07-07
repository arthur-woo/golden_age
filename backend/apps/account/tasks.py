import logging
from core.pipeline.account_executor import execute_account_run

logger = logging.getLogger(__name__)


def run_trading_pipeline(account_id: int):
    """
    스케줄러에 의해 주기적으로 호출되는 최상위 트레이딩 파이프라인 태스크입니다.
    """
    logger.info("스케줄러 작업 시작: 계좌 ID %d 트레이딩 파이프라인 실행", account_id)
    try:
        execute_account_run(account_id)
    except Exception as e:
        logger.exception("스케줄러 실행 중 처리되지 않은 치명적인 에러 발생: %s", e)
