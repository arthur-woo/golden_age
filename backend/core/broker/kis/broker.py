from decimal import Decimal
from typing import Optional

from apps.account.models import Account
from core.broker.interfaces import BaseBroker
from core.broker.dtos import AccountBalanceDTO, PriceDTO, OrderResultDTO
from .client import KISClient


class KoreaInvestmentBroker(BaseBroker):
    """한국투자증권 Broker 구현체"""

    def __init__(self, account: Account):
        self.client = KISClient(account)

    def get_balance(self) -> AccountBalanceDTO:
        # TR_ID는 모의투자와 실전투자가 다를 수 있음 (예: VTTC8434R, TTTC8434R)
        tr_id = "VTTC8434R" if self.client.account.account_type == Account.AccountType.PAPER else "TTTC8434R"
        
        headers = {"tr_id": tr_id}
        params = {
            "CANO": self.client.account.account_number[:8],
            "ACNT_PRDT_CD": self.client.account.account_number[8:] if len(self.client.account.account_number) > 8 else "01",
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
            params=params
        )
        data = response.json()

        # TODO: 응답 코드(rt_cd) 확인 및 예외 처리
        
        # 잔고 추출 (가정된 응답 구조)
        output2 = data.get("output2", [{}])[0]
        cash_balance = Decimal(output2.get("dnca_tot_amt", "0"))  # 예수금
        total_asset_value = Decimal(output2.get("tot_evlu_amt", "0"))  # 총평가금액

        return AccountBalanceDTO(
            cash_balance=cash_balance,
            total_asset_value=total_asset_value,
            raw_payload=data
        )

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
            params=params
        )
        data = response.json()

        output = data.get("output", {})
        price = Decimal(output.get("stck_prpr", "0"))
        volume = Decimal(output.get("acml_vol", "0"))

        return PriceDTO(
            symbol=symbol,
            price=price,
            volume=volume,
            raw_payload=data
        )

    def create_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Optional[Decimal] = None
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

        headers = {"tr_id": tr_id}
        payload = {
            "CANO": self.client.account.account_number[:8],
            "ACNT_PRDT_CD": self.client.account.account_number[8:] if len(self.client.account.account_number) > 8 else "01",
            "PDNO": symbol,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(int(quantity)),
            "ORD_UNPR": ord_unpr,
        }

        response = self.client.request(
            method="POST",
            path="/uapi/domestic-stock/v1/trading/order-cash",
            headers=headers,
            json=payload
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
            raw_payload=data
        )

    def get_minute_chart(self, symbol: str, period: int = 5) -> "pd.DataFrame":
        """당일 분봉 데이터 조회 (KIS API)"""
        import pandas as pd
        from datetime import datetime
        
        headers = {"tr_id": "FHKST03010200"}
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_HOUR_1": datetime.now().strftime("%H%M%S"),
            "FID_PW_DATA_INCU_YN": "N",
        }

        response = self.client.request(
            method="GET",
            path="/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            headers=headers,
            params=params
        )
        data = response.json()
        
        output2 = data.get("output2", [])
        if not output2:
            return pd.DataFrame()
            
        # KIS API 분봉 응답을 DataFrame으로 변환
        # 응답 필드: stck_bsop_date(일자), stck_cntg_hour(체결시간), stck_prpr(현재가/종가), stck_oprc(시가), stck_hgpr(고가), stck_lwpr(저가), cntg_vol(체결거래량)
        df = pd.DataFrame(output2)
        
        df['date'] = pd.to_datetime(df['stck_bsop_date'] + df['stck_cntg_hour'], format='%Y%m%d%H%M%S')
        df['open'] = df['stck_oprc'].astype(float)
        df['high'] = df['stck_hgpr'].astype(float)
        df['low'] = df['stck_lwpr'].astype(float)
        df['close'] = df['stck_prpr'].astype(float)
        df['volume'] = df['cntg_vol'].astype(float)
        
        # 필요한 컬럼만 추출
        df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
        
        # 시간 오름차순(과거->현재)으로 정렬 (보통 KIS는 최신순으로 줌)
        df = df.sort_values('date').reset_index(drop=True)
        
        # KIS는 기본적으로 1분봉이나 5분봉 등을 주는 파라미터가 없거나 다를 수 있습니다.
        # 기본적으로 리샘플링하여 5분봉으로 맞춥니다.
        df.set_index('date', inplace=True)
        df = df.resample(f'{period}min', label='right', closed='right').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna().reset_index()
        
        return df

