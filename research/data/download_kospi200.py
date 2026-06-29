import os
import sys
import time
from datetime import datetime, timedelta
import FinanceDataReader as fdr

def get_kospi200_tickers():
    print("Fetching KOSPI 200 ticker list...")
    df_krx = fdr.StockListing('KOSPI')
    # 코스피 시가총액 상위 200개 추출
    df_krx = df_krx.sort_values(by='Marcap', ascending=False).head(200)
    tickers = df_krx['Code'].tolist()
    print(f"Successfully fetched {len(tickers)} tickers.")
    return tickers

def download_all_data():
    tickers = get_kospi200_tickers()
    
    save_dir = os.path.join(os.path.dirname(__file__), "csv_data")
    os.makedirs(save_dir, exist_ok=True)
    
    # 정확히 1년 전 날짜 계산
    end_date = datetime.today()
    start_date = end_date - timedelta(days=365)
    
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    
    print(f"\n[{start_str} ~ {end_str}] 코스피 시가총액 상위 200종목 1년 치 일봉 데이터 수집 시작...")
    
    success_count = 0
    fail_count = 0
    
    for i, symbol in enumerate(tickers):
        print(f"[{i+1}/{len(tickers)}] Fetching {symbol} ... ", end="")
        try:
            # FinanceDataReader를 사용하여 네이버/KRX 데이터로 1년치 일봉을 한 번에 가져옴
            df = fdr.DataReader(symbol, start_str, end_str)
            
            if not df.empty:
                df = df.reset_index() # Date를 컬럼으로
                df.rename(columns={'Date': 'date', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
                
                file_path = os.path.join(save_dir, f"{symbol}.csv")
                df.to_csv(file_path, index=False)
                print(f"✅ 완료 (데이터 길이: {len(df)}일)")
                success_count += 1
            else:
                print("❌ 실패 (데이터 없음)")
                fail_count += 1
                
        except Exception as e:
            print(f"❌ 에러 발생: {e}")
            fail_count += 1
            
        time.sleep(0.1) # 네이버/KRX 서버 차단 방지 (아주 짧은 딜레이)
        
    print(f"\n🎉 데이터 수집 완료! (성공: {success_count}, 실패: {fail_count})")
    print(f"데이터 저장 위치: {save_dir}/")

if __name__ == "__main__":
    download_all_data()
