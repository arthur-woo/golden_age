import os
import time
import requests
import pandas as pd
from datetime import datetime, timedelta

class KISFetcher:
    """
    한국투자증권 Open API를 통해 과거 데이터를 수집하는 클래스입니다.
    """
    
    # 실전투자: https://openapi.koreainvestment.com:9443
    # 모의투자: https://openapivts.koreainvestment.com:29443
    BASE_URL = "https://openapi.koreainvestment.com:9443"
    
    def __init__(self):
        self.app_key = os.environ.get("KIS_APP_KEY")
        self.app_secret = os.environ.get("KIS_APP_SECRET")
        self.access_token = None
        
        if not self.app_key or not self.app_secret:
            print("WARNING: KIS_APP_KEY or KIS_APP_SECRET is not set in environment variables.")
            
    def issue_token(self):
        """
        접근 토큰을 발급받습니다.
        """
        url = f"{self.BASE_URL}/oauth2/tokenP"
        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }
        res = requests.post(url, headers=headers, json=body)
        if res.status_code == 200:
            self.access_token = res.json().get("access_token")
            print("Token issued successfully.")
        else:
            print(f"Failed to issue token: {res.text}")
            
    def fetch_daily_ohlcv(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        일봉 데이터를 수집합니다. (국내주식 기간별 시세 - FHKST03010100)
        start_date, end_date format: YYYYMMDD
        """
        if not self.access_token:
            print("Access token is required. Call issue_token() first. Returning empty DataFrame.")
            # return empty for testing structure without keys
            return pd.DataFrame()
            
        url = f"{self.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": "FHKST03010100", # 실전투자 기준 일봉 TR ID
            "custtype": "P"
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0" # 수정주가 반영 여부 (0: 반영)
        }
        
        res = requests.get(url, headers=headers, params=params)
        if res.status_code != 200:
            print(f"Failed to fetch data for {symbol}: {res.text}")
            return pd.DataFrame()
            
        data = res.json()
        if "output2" not in data:
            print(f"Unexpected API response structure: {data}")
            return pd.DataFrame()
            
        df = pd.DataFrame(data["output2"])
        if df.empty:
            return df
            
        # 컬럼 매핑 (한투 API 응답 기준)
        # stck_bsop_date: 영업일자, stck_oprc: 시가, stck_hgpr: 고가, stck_lwpr: 저가, stck_clpr: 종가, acml_vol: 누적 거래량
        df = df.rename(columns={
            "stck_bsop_date": "date",
            "stck_oprc": "open",
            "stck_hgpr": "high",
            "stck_lwpr": "low",
            "stck_clpr": "close",
            "acml_vol": "volume"
        })
        
        # 타입 변환
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values("date").reset_index(drop=True)
        return df

    def get_dummy_data(self, symbol: str) -> pd.DataFrame:
        """
        API 키 없이 로직 테스트를 진행하기 위한 가상의 1년치 더미 데이터를 생성합니다.
        """
        import numpy as np
        dates = pd.date_range(end=pd.Timestamp.today(), periods=250, freq='B')
        
        # Random walk for prices
        np.random.seed(42)
        returns = np.random.normal(0, 0.02, 250)
        close_prices = 100000 * np.exp(returns.cumsum())
        
        df = pd.DataFrame({
            'date': dates,
            'open': close_prices * np.random.uniform(0.98, 1.01, 250),
            'high': close_prices * np.random.uniform(1.0, 1.03, 250),
            'low': close_prices * np.random.uniform(0.97, 1.0, 250),
            'close': close_prices,
            'volume': np.random.randint(100000, 1000000, 250)
        })
        df['high'] = df[['open', 'close', 'high']].max(axis=1)
        df['low'] = df[['open', 'close', 'low']].min(axis=1)
        
        # 정수로 변환
        for col in ['open', 'high', 'low', 'close']:
            df[col] = df[col].astype(int)
            
        return df

if __name__ == "__main__":
    fetcher = KISFetcher()
    # fetcher.issue_token()
    # df = fetcher.fetch_daily_ohlcv("005930", "20230101", "20231231")
    
    # API 키가 없을 때 테스트용
    print("Generating dummy data for testing...")
    df = fetcher.get_dummy_data("005930")
    print(df.head())
    
    # Save to dummy csv
    os.makedirs(os.path.dirname(__file__), exist_ok=True)
    df.to_csv(os.path.join(os.path.dirname(__file__), "dummy_005930.csv"), index=False)
    print("Dummy data saved to research/data/dummy_005930.csv")
