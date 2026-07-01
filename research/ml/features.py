import pandas as pd
import numpy as np

def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    원본 시세 데이터(df)를 받아 머신러닝 모델이 학습할 파생 변수(Feature)들을 생성합니다.
    시그널이 발생한 시점의 입체적인 시장 상태를 포착합니다.
    """
    df_feat = df.copy()
    
    # 1. 시간대 Feature (장 초반/중반/후반 파악)
    if 'date' in df_feat.columns:
        date_col = pd.to_datetime(df_feat['date'])
        df_feat['hour'] = date_col.dt.hour
        df_feat['minute'] = date_col.dt.minute
        # 09:00 = 540, 15:30 = 930
        df_feat['time_val'] = df_feat['hour'] * 60 + df_feat['minute']
        
    # 2. 가격 모멘텀 (과거 1, 3, 5개 분봉 수익률)
    df_feat['ret_1'] = df_feat['close'].pct_change(1)
    df_feat['ret_3'] = df_feat['close'].pct_change(3)
    df_feat['ret_5'] = df_feat['close'].pct_change(5)
    
    # 3. 캔들 형태 (Candle Shape)
    df_feat['body_pct'] = (df_feat['close'] - df_feat['open']) / df_feat['open'].replace(0, np.nan)
    df_feat['high_shadow_pct'] = (df_feat['high'] - df_feat[['open', 'close']].max(axis=1)) / df_feat['open'].replace(0, np.nan)
    df_feat['low_shadow_pct'] = (df_feat[['open', 'close']].min(axis=1) - df_feat['low']) / df_feat['open'].replace(0, np.nan)
    
    # 4. 이동평균선 이격도 (Disparity)
    df_feat['ma_20'] = df_feat['close'].rolling(20).mean()
    df_feat['ma_60'] = df_feat['close'].rolling(60).mean()
    df_feat['disp_20'] = df_feat['close'] / df_feat['ma_20'].replace(0, np.nan)
    df_feat['disp_60'] = df_feat['close'] / df_feat['ma_60'].replace(0, np.nan)
    
    # 5. 볼린저밴드 상대 위치 (0.0=하단, 1.0=상단)
    std_20 = df_feat['close'].rolling(20).std()
    df_feat['bb_upper'] = df_feat['ma_20'] + 2 * std_20
    df_feat['bb_lower'] = df_feat['ma_20'] - 2 * std_20
    # 밴드폭이 0인 경우 방지
    band_width = (df_feat['bb_upper'] - df_feat['bb_lower']).replace(0, np.nan)
    df_feat['bb_pct'] = (df_feat['close'] - df_feat['bb_lower']) / band_width
    
    # 6. 거래량 특징 (Volume Ratio)
    vol_ma_20 = df_feat['volume'].rolling(20).mean()
    df_feat['vol_ratio'] = df_feat['volume'] / vol_ma_20.replace(0, np.nan)
    
    # 7. RSI (14)
    delta = df_feat['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df_feat['rsi_14'] = 100 - (100 / (1 + rs))
    
    # 학습에 사용할 특성 리스트 명시 (나머지 drop)
    feature_cols = [
        'time_val', 'ret_1', 'ret_3', 'ret_5', 
        'body_pct', 'high_shadow_pct', 'low_shadow_pct',
        'disp_20', 'disp_60', 'bb_pct', 'vol_ratio', 'rsi_14'
    ]
    
    return df_feat, feature_cols
