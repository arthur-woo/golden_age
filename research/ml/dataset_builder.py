import os
import sys
import glob
import pandas as pd
import numpy as np
from tqdm import tqdm

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from research.strategies.sample_strategies import IntradayDojiVolumeStrategy, MovingAverageBreakoutStrategy
from research.ml.features import compute_features

def build_dataset():
    data_dir = os.path.join(os.path.dirname(__file__), '../data/intraday_csv')
    csv_files = glob.glob(os.path.join(data_dir, "*.csv"))
    
    # 1위 전략 인스턴스 (도지+거래량 3.0x (0.4) + MA60 돌파 (0.6))
    stg_doji = IntradayDojiVolumeStrategy(vol_mult=3.0)
    stg_ma = MovingAverageBreakoutStrategy(window=60)
    weight_doji = 0.4
    weight_ma = 0.6
    threshold = 20.0
    
    fee_rate = 0.00015
    tax_rate = 0.002
    slippage = 0.0005
    trailing_stop_pct = 0.015
    
    dataset_rows = []
    
    print(f"Building ML Dataset from {len(csv_files)} stocks...")
    
    for file in tqdm(csv_files):
        df = pd.read_csv(file)
        if len(df) < 100:
            continue
            
        # 1. Feature 연산
        df_feat, feature_cols = compute_features(df)
        
        # 2. 시그널 연산
        sig_doji = stg_doji.generate_signals(df)
        sig_ma = stg_ma.generate_signals(df)
        
        dec_doji = sig_doji['decision'].astype(float).fillna(0).values
        score_doji = sig_doji['score'].astype(float).fillna(0).values
        
        dec_ma = sig_ma['decision'].astype(float).fillna(0).values
        score_ma = sig_ma['score'].astype(float).fillna(0).values
        
        mixed_score = (dec_doji * score_doji * weight_doji) + (dec_ma * score_ma * weight_ma)
        
        df_feat['mixed_raw_score'] = mixed_score
        
        decision = np.zeros(len(df_feat))
        decision[mixed_score > threshold] = 1
        decision[mixed_score < -threshold] = -1
        
        # 강제 청산 시그널 (-1) 병합
        force_sell_mask = ((sig_doji['decision'].values == -1) & (sig_doji['score'].values == 100.0)) | \
                          ((sig_ma['decision'].values == -1) & (sig_ma['score'].values == 100.0))
        decision[force_sell_mask] = -1
        
        df_feat['decision'] = decision
        
        # 3. 매수 시점 포착 및 Forward 시뮬레이션
        # 0: 무포지션, 1: 보유중
        current_position = 0
        entry_price = 0.0
        entry_idx = -1
        high_since_entry = 0.0
        
        closes = df_feat['close'].values
        
        for i in range(len(df_feat)):
            close_price = closes[i]
            dec = decision[i]
            
            # 트레일링 스탑 체크
            if current_position > 0:
                high_since_entry = max(high_since_entry, close_price)
                if close_price < high_since_entry * (1.0 - trailing_stop_pct):
                    dec = -1
                    
            if dec == 1 and current_position == 0:
                # 진입
                execution_price = close_price * (1.0 + slippage)
                entry_price = execution_price
                current_position = 1
                entry_idx = i
                high_since_entry = execution_price
                
            elif dec == -1 and current_position > 0:
                # 청산
                execution_price = close_price * (1.0 - slippage)
                
                # 손익 계산
                # 매수 금액 100만원 가정
                buy_amount = 1000000.0
                fee_buy = buy_amount * fee_rate
                shares = (buy_amount - fee_buy) / entry_price
                
                sell_amount = shares * execution_price
                fee_sell = sell_amount * fee_rate
                tax_sell = sell_amount * tax_rate
                
                final_cash = sell_amount - fee_sell - tax_sell
                profit = final_cash - buy_amount
                
                # 정답지 (수익 나면 1, 아니면 0)
                label = 1 if profit > 0 else 0
                
                # 진입 시점(entry_idx)의 Feature 행을 복사하여 라벨 추가
                feat_row = df_feat.iloc[entry_idx][feature_cols].to_dict()
                feat_row['label'] = label
                feat_row['profit'] = profit
                dataset_rows.append(feat_row)
                
                current_position = 0
                entry_price = 0.0
                high_since_entry = 0.0
                entry_idx = -1

    ml_df = pd.DataFrame(dataset_rows)
    # 결측치가 있는 로우 제거 (이평선 등 초반 데이터)
    ml_df = ml_df.dropna()
    
    save_path = os.path.join(os.path.dirname(__file__), 'ml_dataset.csv')
    ml_df.to_csv(save_path, index=False)
    print(f"✅ ML Dataset 생성이 완료되었습니다. (총 {len(ml_df)}개의 거래 케이스, 경로: {save_path})")

if __name__ == "__main__":
    build_dataset()
