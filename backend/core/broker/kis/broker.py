from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Optional

from apps.account.models import Account
from core.broker.interfaces import BaseBroker
from core.broker.dtos import AccountBalanceDTO, PriceDTO, OrderResultDTO
from .client import KISClient

KST = dt_timezone(timedelta(hours=9))  # 한국 표준시


def parse_minute_candles(data: dict) -> list[dict]:
    """
    KIS 분봉 응답(output2)을 파싱한다.

    반환: [{opened_at(datetime, KST), open, high, low, close, volume}], 시간 오름차순.
    """
    rows = data.get("output2", []) or []
    candles: list[dict] = []
    for row in rows:
        d = row.get("stck_bsop_date")
        t = row.get("stck_cntg_hour")
        if not d or not t:
            continue
        opened_at = datetime(
            int(d[:4]),
            int(d[4:6]),
            int(d[6:8]),
            int(t[:2]),
            int(t[2:4]),
            int(t[4:6]),
            tzinfo=KST,
        )
        candles.append(
            {
                "opened_at": opened_at,
                "open": Decimal(row["stck_oprc"]),
                "high": Decimal(row["stck_hgpr"]),
                "low": Decimal(row["stck_lwpr"]),
                "close": Decimal(row["stck_prpr"]),
                "volume": Decimal(row.get("cntg_vol", "0")),
            }
        )
    # KIS는 최신→과거로 주므로 오름차순 정렬해 반환
    candles.sort(key=lambda c: c["opened_at"])
    return candles


def parse_executions(data: dict) -> list[dict]:
    """
    KIS 일별주문체결조회 응답(output1)을 파싱한다.

    반환: [{order_no, symbol, ordered_qty, filled_qty, avg_price, remaining_qty}]
    """
    rows = data.get("output1", []) or []
    executions: list[dict] = []
    for row in rows:
        executions.append(
            {
                "order_no": row.get("odno", ""),
                "symbol": row.get("pdno", ""),
                "ordered_qty": Decimal(row.get("ord_qty", "0") or "0"),
                "filled_qty": Decimal(row.get("tot_ccld_qty", "0") or "0"),
                "avg_price": Decimal(row.get("avg_prvs", "0") or "0"),
                "remaining_qty": Decimal(row.get("rmn_qty", "0") or "0"),
            }
        )
    return executions


class KoreaInvestmentBroker(BaseBroker):
    """한국투자증권 Broker 구현체"""

    def __init__(self, account: Account):
        self.client = KISClient(account)

    def _inquire_balance(self) -> dict:
        """잔고조회 원본 응답(output1=보유종목, output2=예수금/평가)을 반환한다."""
        # TR_ID는 모의투자와 실전투자가 다를 수 있음 (예: VTTC8434R, TTTC8434R)
        tr_id = (
            "VTTC8434R"
            if self.client.account.account_type == Account.AccountType.PAPER
            else "TTTC8434R"
        )
        headers = {"tr_id": tr_id, "custtype": "P"}
        params = {
            "CANO": self.client.account.account_number[:8],
            "ACNT_PRDT_CD": self.client.account.account_number[8:]
            if len(self.client.account.account_number) > 8
            else "01",
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        response = self.client.request(
            method="GET",
            path="/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=headers,
            params=params,
        )
        return response.json()

    def get_balance(self) -> AccountBalanceDTO:
        data = self._inquire_balance()
        output2 = (data.get("output2") or [{}])[0]
        cash_balance = Decimal(output2.get("dnca_tot_amt", "0"))  # 예수금
        total_asset_value = Decimal(output2.get("tot_evlu_amt", "0"))  # 총평가금액
        return AccountBalanceDTO(
            cash_balance=cash_balance,
            total_asset_value=total_asset_value,
            raw_payload=data,
        )

    def get_positions(self) -> dict:
        """브로커 보유 수량 {symbol: qty>0}. 리컨실리에이션용."""
        data = self._inquire_balance()
        positions = {}
        for row in data.get("output1", []) or []:
            symbol = row.get("pdno")
            qty = Decimal(row.get("hldg_qty", "0") or "0")
            if symbol and qty > 0:
                positions[symbol] = qty
        return positions

    def get_current_price(self, symbol: str) -> PriceDTO:
        headers = {"tr_id": "FHKST01010100"}
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
        }

        response = self.client.request(
            method="GET",
            path="/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=headers,
            params=params,
        )
        data = response.json()

        output = data.get("output", {})
        price = Decimal(output.get("stck_prpr", "0"))
        volume = Decimal(output.get("acml_vol", "0"))

        return PriceDTO(symbol=symbol, price=price, volume=volume, raw_payload=data)

    def get_minute_candles(self, symbol: str, to_time: str = "") -> list[dict]:
        """
        주식 당일 분봉 조회 (최근 ~30건, to_time HHMMSS 이전).

        to_time을 앞으로 당겨가며 반복 호출하면 하루치를 백필할 수 있다.
        """
        headers = {"tr_id": "FHKST03010200", "custtype": "P"}
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_HOUR_1": to_time,
            "FID_PW_DATA_INCU_YN": "Y",
        }
        response = self.client.request(
            method="GET",
            path="/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            headers=headers,
            params=params,
        )
        return parse_minute_candles(response.json())

    def create_order(
        self, symbol: str, side: str, quantity: Decimal, price: Optional[Decimal] = None
    ) -> OrderResultDTO:
        # 매수/매도 TR_ID 판별
        is_paper = self.client.account.account_type == Account.AccountType.PAPER
        if side == "BUY":
            tr_id = "VTTC0802U" if is_paper else "TTTC0802U"
        else:
            tr_id = "VTTC0801U" if is_paper else "TTTC0801U"

        # 주문 구분 (시장가/지정가)
        ord_dvsn = "01" if price is None else "00"
        ord_unpr = "0" if price is None else str(int(price))

        payload = {
            "CANO": self.client.account.account_number[:8],
            "ACNT_PRDT_CD": self.client.account.account_number[8:]
            if len(self.client.account.account_number) > 8
            else "01",
            "PDNO": symbol,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(int(quantity)),
            "ORD_UNPR": ord_unpr,
        }

        # KIS 주문 POST는 hashkey(본문 무결성) + custtype 헤더가 필요하다.
        headers = {
            "tr_id": tr_id,
            "custtype": "P",
            "hashkey": self.client.get_hashkey(payload),
        }

        response = self.client.request(
            method="POST",
            path="/uapi/domestic-stock/v1/trading/order-cash",
            headers=headers,
            json=payload,
        )
        data = response.json()

        success = data.get("rt_cd") == "0"
        error_message = data.get("msg1") if not success else None

        output = data.get("output", {})
        order_id = output.get("ODNO") if output else None

        return OrderResultDTO(
            success=success,
            order_id=order_id,
            error_message=error_message,
            raw_payload=data,
        )

    def get_order_execution(
        self,
        order_no: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> list[dict]:
        """
        주식 일별 주문체결 조회. order_no 지정 시 해당 주문의 체결 내역만 필터.

        start_date/end_date(YYYYMMDD) 미지정 시 당일(KST)로 조회한다.
        """
        today = datetime.now(KST).strftime("%Y%m%d")
        start_date = start_date or today
        end_date = end_date or today

        is_paper = self.client.account.account_type == Account.AccountType.PAPER
        tr_id = "VTTC8001R" if is_paper else "TTTC8001R"

        headers = {"tr_id": tr_id, "custtype": "P"}
        params = {
            "CANO": self.client.account.account_number[:8],
            "ACNT_PRDT_CD": self.client.account.account_number[8:]
            if len(self.client.account.account_number) > 8
            else "01",
            "INQR_STRT_DT": start_date,
            "INQR_END_DT": end_date,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": order_no,
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        response = self.client.request(
            method="GET",
            path="/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            headers=headers,
            params=params,
        )
        return parse_executions(response.json())
