import os
import yfinance as yf
import pandas as pd

def download_intraday_data():
    """
    야후 파이낸스(yfinance)를 사용하여 무료로 한국 주식의 5분봉 데이터를 다운로드합니다.
    머신러닝 학습 데이터를 확보하기 위해 코스피 우량주 20개로 대상을 확대합니다.
    """
    tickers_map = {
        "005930.KS": "005930", "000660.KS": "000660", "373220.KS": "373220", 
        "207940.KS": "207940", "005380.KS": "005380", "000270.KS": "000270", 
        "068270.KS": "068270", "005490.KS": "005490", "051910.KS": "051910", 
        "105560.KS": "105560", "035420.KS": "035420", "028260.KS": "028260", 
        "006400.KS": "006400", "012330.KS": "012330", "032830.KS": "032830", 
        "066570.KS": "066570", "323410.KS": "323410", "011200.KS": "011200", 
        "316140.KS": "316140", "035720.KS": "035720"
    }
    
    save_dir = os.path.join(os.path.dirname(__file__), "intraday_csv")
    os.makedirs(save_dir, exist_ok=True)
    
    print("📈 단타 시뮬레이션용 5분봉 데이터 다운로드 시작 (최근 60영업일)...")
    
    for yf_ticker, krx_code in tickers_map.items():
        print(f"Fetching 5-min data for {krx_code} ({yf_ticker}) ...", end=" ")
        try:
            # interval="5m", period="60d" (yfinance에서 5분봉은 최대 60일까지만 제공)
            df = yf.download(yf_ticker, period="60d", interval="5m", progress=False)
            
            if not df.empty:
                # yfinance 멀티인덱스 컬럼 정리 (단일 종목 다운로드 시에도 가끔 멀티인덱스로 나옴)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.droplevel(1)
                    
                df = df.reset_index()
                # 컬럼명 통일
                df.rename(columns={'Datetime': 'date', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
                
                # 'date' 컬럼 타임존 제거 (한국 시간 기준으로 변환)
                if pd.api.types.is_datetime64tz_dtype(df['date']):
                    df['date'] = df['date'].dt.tz_convert('Asia/Seoul').dt.tz_localize(None)
                
                file_path = os.path.join(save_dir, f"{krx_code}_5m.csv")
                df.to_csv(file_path, index=False)
                print(f"✅ 완료 (데이터 길이: {len(df)}개 분봉)")
            else:
                print("❌ 실패 (데이터 없음)")
        except Exception as e:
            print(f"❌ 에러 발생: {e}")

if __name__ == "__main__":
    download_intraday_data()
