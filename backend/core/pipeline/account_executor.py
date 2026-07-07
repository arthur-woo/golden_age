import logging
from django.utils import timezone
from django.db import transaction

from apps.account.models import Account, ExecutionRun, BalanceSnapshot
from apps.stock.models import Stock
from apps.trading.models import Trader, TraderExecutionRun
from apps.account.services import get_broker_for_account
from core.analyzer.market_analyzer import analyze_regime
from core.pipeline.trader_executor import execute_trader_for_stock

logger = logging.getLogger(__name__)


def execute_account_run(account_id: int, run_type: str = ExecutionRun.RunType.SCHEDULED):
    """
    특정 계좌의 자동 매매 라이프사이클을 전체적으로 구동합니다.
    """
    try:
        account = Account.objects.get(id=account_id, is_active=True)
    except Account.DoesNotExist:
        logger.error("계좌를 찾을 수 없거나 비활성화 상태입니다. (ID: %d)", account_id)
        return

    # 1. ExecutionRun 시작 기록
    account_run = ExecutionRun.objects.create(
        account=account,
        run_type=run_type,
        status=ExecutionRun.Status.RUNNING,
        started_at=timezone.now(),
    )

    logger.info("[%s] 계좌 트레이딩 파이프라인 시작 (Run ID: %d)", account, account_run.id)

    try:
        # 2. 거래 대상 종목 로드
        active_stocks = Stock.objects.filter(is_active=True)
        if not active_stocks.exists():
            logger.warning("[%s] 거래 가능한 활성 종목이 존재하지 않습니다.", account)

        # 3. 각 종목에 대한 시장 국면 분석 (RegimeSnapshot 생성)
        regime_snapshots = {}
        for stock in active_stocks:
            try:
                # 일봉 캔들 기반으로 시장 국면 분석
                regime_snapshots[stock] = analyze_regime(stock)
            except Exception as e:
                logger.exception("[%s] 종목 %s 시장 분석 에러: %s", account, stock.symbol, e)
                regime_snapshots[stock] = None

        # 4. 활성화된 Trader 로드
        traders = account.traders.filter(status=Trader.Status.ACTIVE)
        if not traders.exists():
            logger.warning("[%s] 활성화된 Trader가 존재하지 않습니다.", account)

        # 5. 각 종목별로 각 Trader 실행
        for stock in active_stocks:
            regime_snapshot = regime_snapshots.get(stock)
            
            for trader in traders:
                trader_run = TraderExecutionRun.objects.create(
                    account_run=account_run,
                    trader=trader,
                    status=TraderExecutionRun.Status.RUNNING,
                    started_at=timezone.now(),
                )
                
                try:
                    execute_trader_for_stock(
                        trader=trader,
                        trader_run=trader_run,
                        stock=stock,
                        regime_snapshot=regime_snapshot,
                    )
                    trader_run.status = TraderExecutionRun.Status.SUCCESS
                except Exception as e:
                    logger.exception("[%s] Trader %s 실행 중 오류 발생: %s", account, trader.name, e)
                    trader_run.status = TraderExecutionRun.Status.FAILED
                    trader_run.error_message = str(e)
                finally:
                    trader_run.finished_at = timezone.now()
                    trader_run.save(update_fields=["status", "error_message", "finished_at"])

        # 6. 최종 계좌 잔고 스냅샷 기록
        try:
            broker = get_broker_for_account(account)
            balance = broker.get_balance()
            
            BalanceSnapshot.objects.create(
                account=account,
                cash_balance=balance.cash_balance,
                total_asset_value=balance.total_asset_value,
                snapshot_payload=balance.raw_payload if isinstance(balance.raw_payload, dict) else {},
                snapshotted_at=timezone.now(),
            )
        except Exception as e:
            logger.error("[%s] 최종 잔고 스냅샷 저장 실패: %s", account, e)

        # 7. 전체 실행 성공 기록
        account_run.status = ExecutionRun.Status.SUCCESS

    except Exception as e:
        logger.exception("[%s] 계좌 트레이딩 파이프라인 진행 중 예외 발생: %s", account, e)
        account_run.status = ExecutionRun.Status.FAILED
        account_run.error_message = str(e)

    finally:
        account_run.finished_at = timezone.now()
        account_run.save(update_fields=["status", "error_message", "finished_at"])
        logger.info("[%s] 계좌 트레이딩 파이프라인 종료 (Status: %s)", account, account_run.status)
