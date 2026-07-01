import sys
import os
import yfinance as yf
import pandas as pd
import joblib

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from research.strategies.sample_strategies import IntradayDojiVolumeStrategy, MovingAverageBreakoutStrategy
from research.ml.features import compute_features

def main():
    tickers = {
        "LS ELECTRIC": "010120.KS",
        "가온전선": "000500.KS",
        "HD현대중공업": "329180.KS",
        "삼성중공업": "010140.KS",
        "한미반도체": "042700.KS",
        "한화에어로스페이스": "012450.KS",
        "현대로템": "064350.KS",
        "SK이터닉스": "475150.KS",
        "한화솔루션": "009830.KS",
        "삼성전기": "009150.KS",
    }
    
    stg_doji = IntradayDojiVolumeStrategy(vol_mult=3.0)
    stg_ma = MovingAverageBreakoutStrategy(window=60)
    weight_doji, weight_ma, threshold = 0.4, 0.6, 20.0
    
    model_path = os.path.join(os.path.dirname(__file__), 'lgbm_filter.pkl')
    try:
        model = joblib.load(model_path)
    except Exception as e:
        print(f"Model load error: {e}")
        return

    results = []

    for name, ticker_symbol in tickers.items():
        print(f"Processing {name} ({ticker_symbol})...")
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period="60d", interval="5m")
        if df.empty:
            print(f"No data for {name}")
            continue
            
        df = df.reset_index()
        df = df.rename(columns={
            'Datetime': 'date', 'Open': 'open', 'High': 'high', 
            'Low': 'low', 'Close': 'close', 'Volume': 'volume'
        })
        if df['date'].dt.tz is not None:
            df['date'] = df['date'].dt.tz_convert('Asia/Seoul').dt.tz_localize(None)
        
        # Calculate features early
        df_feats_all, feat_cols = compute_features(df)
        df_feats = df_feats_all[feat_cols]
        
        sig_doji = stg_doji.generate_signals(df)
        sig_ma = stg_ma.generate_signals(df)
        
        # Forward simulate to find actual signals
        baseline_trades = 0
        baseline_wins = 0
        
        a_type_trades = 0
        a_type_wins = 0
        
        b_type_trades = 0
        b_type_wins = 0
        
        in_position = False
        entry_price = 0
        entry_time_idx = 0
        target_sell = 0
        stop_sell = 0
        
        # We need to loop through dataframe and simulate
        for i in range(60, len(df)):
            current_price = df.iloc[i]['close']
            
            # Simple simulation: if we have signal, check if it's a win or loss
            dec_doji = sig_doji.iloc[i]['decision']
            score_doji = sig_doji.iloc[i]['score']
            dec_ma = sig_ma.iloc[i]['decision']
            score_ma = sig_ma.iloc[i]['score']
            
            mixed_score = (float(dec_doji or 0) * float(score_doji or 0) * weight_doji) + \
                          (float(dec_ma or 0) * float(score_ma or 0) * weight_ma)
                          
            if mixed_score > threshold and not in_position:
                # We have a buy signal
                entry_price = current_price
                # Check ML
                feat_row = df_feats.iloc[[i]]
                if not feat_row.isnull().values.any():
                    prob = float(model.predict_proba(feat_row)[0][1])
                else:
                    prob = 0.0
                    
                # To see if it's a win, look ahead up to 10 periods
                is_win = False
                for j in range(i+1, min(i+11, len(df))):
                    future_price = df.iloc[j]['close']
                    # stop loss
                    if future_price < entry_price * 0.985:
                        is_win = False
                        break
                    if future_price > entry_price * 1.01:
                        is_win = True
                        break
                        
                baseline_trades += 1
                if is_win: baseline_wins += 1
                
                if prob >= 0.70:
                    a_type_trades += 1
                    if is_win: a_type_wins += 1
                
                if prob >= 0.50:
                    b_type_trades += 1
                    if is_win: b_type_wins += 1

        res = {
            "name": name,
            "baseline_count": baseline_trades,
            "baseline_win": f"{baseline_wins/baseline_trades*100:.1f}%" if baseline_trades > 0 else "0.0%",
            "b_type_count": b_type_trades,
            "b_type_win": f"{b_type_wins/b_type_trades*100:.1f}%" if b_type_trades > 0 else "0.0%",
            "a_type_count": a_type_trades,
            "a_type_win": f"{a_type_wins/a_type_trades*100:.1f}%" if a_type_trades > 0 else "0.0%",
        }
        results.append(res)
        
    print("\n--- 분석 결과 리포트 ---")
    for r in results:
        print(f"[{r['name']}] Baseline: {r['baseline_count']}회({r['baseline_win']}) | B타입(0.5): {r['b_type_count']}회({r['b_type_win']}) | A타입(0.7): {r['a_type_count']}회({r['a_type_win']})")

if __name__ == "__main__":
    main()
