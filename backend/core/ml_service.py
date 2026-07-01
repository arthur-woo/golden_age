import os
import joblib
import pandas as pd
import numpy as np

class MLFilterService:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MLFilterService, cls).__new__(cls)
            cls._instance._load_model()
        return cls._instance

    def _load_model(self):
        model_path = os.path.join(os.path.dirname(__file__), 'ml', 'lgbm_filter.pkl')
        try:
            self.model = joblib.load(model_path)
        except Exception as e:
            print(f"Failed to load ML model from {model_path}: {e}")
            self.model = None

    def compute_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        원본 5분봉 시세 데이터를 받아 머신러닝 모델이 학습했던 
        파생 변수(Feature)들을 정확히 똑같은 방식으로 생성합니다.
        """
        df_feat = df.copy()
        
        # 1. 시간대 Feature
        if 'date' in df_feat.columns:
            date_col = pd.to_datetime(df_feat['date'])
            df_feat['hour'] = date_col.dt.hour
            df_feat['minute'] = date_col.dt.minute
            df_feat['time_val'] = df_feat['hour'] * 60 + df_feat['minute']
            
        # 2. 가격 모멘텀
        df_feat['ret_1'] = df_feat['close'].pct_change(1)
        df_feat['ret_3'] = df_feat['close'].pct_change(3)
        df_feat['ret_5'] = df_feat['close'].pct_change(5)
        
        # 3. 캔들 형태
        df_feat['body_pct'] = (df_feat['close'] - df_feat['open']) / df_feat['open'].replace(0, np.nan)
        df_feat['high_shadow_pct'] = (df_feat['high'] - df_feat[['open', 'close']].max(axis=1)) / df_feat['open'].replace(0, np.nan)
        df_feat['low_shadow_pct'] = (df_feat[['open', 'close']].min(axis=1) - df_feat['low']) / df_feat['open'].replace(0, np.nan)
        
        # 4. 이동평균선 이격도
        df_feat['ma_20'] = df_feat['close'].rolling(20).mean()
        df_feat['ma_60'] = df_feat['close'].rolling(60).mean()
        df_feat['disp_20'] = df_feat['close'] / df_feat['ma_20'].replace(0, np.nan)
        df_feat['disp_60'] = df_feat['close'] / df_feat['ma_60'].replace(0, np.nan)
        
        # 5. 볼린저밴드 상대 위치
        std_20 = df_feat['close'].rolling(20).std()
        df_feat['bb_upper'] = df_feat['ma_20'] + 2 * std_20
        df_feat['bb_lower'] = df_feat['ma_20'] - 2 * std_20
        band_width = (df_feat['bb_upper'] - df_feat['bb_lower']).replace(0, np.nan)
        df_feat['bb_pct'] = (df_feat['close'] - df_feat['bb_lower']) / band_width
        
        # 6. 거래량 특징
        vol_ma_20 = df_feat['volume'].rolling(20).mean()
        df_feat['vol_ratio'] = df_feat['volume'] / vol_ma_20.replace(0, np.nan)
        
        # 7. RSI (14)
        delta = df_feat['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        df_feat['rsi_14'] = 100 - (100 / (1 + rs))
        
        feature_cols = [
            'time_val', 'ret_1', 'ret_3', 'ret_5', 
            'body_pct', 'high_shadow_pct', 'low_shadow_pct',
            'disp_20', 'disp_60', 'bb_pct', 'vol_ratio', 'rsi_14'
        ]
        
        return df_feat[feature_cols]

    def predict_prob(self, df: pd.DataFrame) -> float:
        """
        주어진 데이터프레임(최근 60개 봉 이상 포함 권장)의 가장 마지막(최신) 시점에 대해 
        성공 확률(0.0 ~ 1.0)을 예측하여 반환합니다.
        """
        if self.model is None:
            print("Model not loaded. Defaulting to prob=0.0")
            return 0.0
            
        try:
            # 1. Feature 계산
            features_df = self.compute_features(df)
            
            # 2. 마지막 행 추출 (현재 시점)
            latest_features = features_df.iloc[[-1]]
            
            # 결측치가 있으면 예측 불가
            if latest_features.isnull().values.any():
                print("Missing values in features. Cannot predict.")
                return 0.0
                
            # 3. 확률 추론 (class 1이 성공할 확률)
            prob = self.model.predict_proba(latest_features)[0][1]
            return float(prob)
            
        except Exception as e:
            print(f"Prediction error: {e}")
            return 0.0
