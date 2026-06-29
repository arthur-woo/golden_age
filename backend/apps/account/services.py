from apps.account.models import Account
from core.broker.interfaces import BaseBroker
from core.broker.kis.broker import KoreaInvestmentBroker

def get_broker_for_account(account: Account) -> BaseBroker:
    """
    주어진 Account 인스턴스에 적합한 Broker 구현체를 반환한다.
    """
    if account.broker == Account.Broker.KIS:
        return KoreaInvestmentBroker(account)
    
    raise ValueError(f"지원하지 않는 브로커입니다: {account.broker}")
