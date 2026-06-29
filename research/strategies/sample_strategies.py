import pandas as pd
import numpy as np
from .base import BaseStrategy

class MovingAverageBreakoutStrategy(BaseStrategy):
    def __init__(self, window: int = 20):
        super().__init__(name=f"MA_Breakout_{window}", window=window)
        self.window = window

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signals = pd.DataFrame(index=df.index)
        signals['decision'] = 0
        signals['score'] = 0.0
        
        ma = df['close'].rolling(window=self.window).mean()
        prev_close = df['close'].shift(1)
        prev_ma = ma.shift(1)
        
        buy_cond = (prev_close < prev_ma) & (df['close'] > ma)
        sell_cond = (prev_close > prev_ma) & (df['close'] < ma)
        
        signals.loc[buy_cond, 'decision'] = 1
        signals.loc[sell_cond, 'decision'] = -1
        
        deviation = abs(df['close'] - ma) / ma * 100
        score = np.clip(deviation * 20, 0, 100)
        signals.loc[buy_cond | sell_cond, 'score'] = score[buy_cond | sell_cond]
        
        return signals

class RSIStrategy(BaseStrategy):
    def __init__(self, window: int = 14, oversold: int = 30, overbought: int = 70):
        super().__init__(name=f"RSI_{window}", window=window, oversold=oversold, overbought=overbought)
        self.window = window
        self.oversold = oversold
        self.overbought = overbought

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signals = pd.DataFrame(index=df.index)
        signals['decision'] = 0
        signals['score'] = 0.0
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.window).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.window).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        buy_cond = (rsi < self.oversold)
        sell_cond = (rsi > self.overbought)
        
        signals.loc[buy_cond, 'decision'] = 1
        signals.loc[sell_cond, 'decision'] = -1
        
        signals.loc[buy_cond, 'score'] = np.clip((self.oversold - rsi) / self.oversold * 100, 0, 100)[buy_cond]
        signals.loc[sell_cond, 'score'] = np.clip((rsi - self.overbought) / (100 - self.overbought) * 100, 0, 100)[sell_cond]
        
        return signals

class MomentumBreakoutStrategy(BaseStrategy):
    def __init__(self, window: int = 20):
        super().__init__(name=f"Momentum_{window}", window=window)
        self.window = window

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signals = pd.DataFrame(index=df.index, columns=['decision', 'score']).fillna(0)
        recent_high = df['high'].shift(1).rolling(window=self.window).max()
        recent_low = df['low'].shift(1).rolling(window=self.window).min()
        
        buy_cond = df['close'] > recent_high
        sell_cond = df['close'] < recent_low
        
        signals.loc[buy_cond, 'decision'] = 1
        signals.loc[sell_cond, 'decision'] = -1
        signals.loc[buy_cond | sell_cond, 'score'] = 100.0 # 강한 돌파는 항상 100점
        return signals

class MAGoldenCrossStrategy(BaseStrategy):
    def __init__(self, short_win: int = 5, long_win: int = 20):
        super().__init__(name=f"GCross_{short_win}_{long_win}", short_win=short_win, long_win=long_win)
        self.short_win = short_win
        self.long_win = long_win

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signals = pd.DataFrame(index=df.index, columns=['decision', 'score']).fillna(0)
        short_ma = df['close'].rolling(window=self.short_win).mean()
        long_ma = df['close'].rolling(window=self.long_win).mean()
        
        prev_short = short_ma.shift(1)
        prev_long = long_ma.shift(1)
        
        buy_cond = (prev_short < prev_long) & (short_ma > long_ma)
        sell_cond = (prev_short > prev_long) & (short_ma < long_ma)
        
        signals.loc[buy_cond, 'decision'] = 1
        signals.loc[sell_cond, 'decision'] = -1
        signals.loc[buy_cond | sell_cond, 'score'] = 80.0
        return signals

class VolumeSpikeStrategy(BaseStrategy):
    def __init__(self, window: int = 20, multiplier: float = 3.0):
        super().__init__(name=f"VolSpike_{multiplier}x", window=window, multiplier=multiplier)
        self.window = window
        self.multiplier = multiplier

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signals = pd.DataFrame(index=df.index, columns=['decision', 'score']).fillna(0)
        avg_vol = df['volume'].shift(1).rolling(window=self.window).mean()
        
        buy_cond = (df['volume'] > avg_vol * self.multiplier) & (df['close'] > df['open'])
        sell_cond = (df['volume'] > avg_vol * self.multiplier) & (df['close'] < df['open'])
        
        signals.loc[buy_cond, 'decision'] = 1
        signals.loc[sell_cond, 'decision'] = -1
        
        # 거래량이 클수록 높은 점수
        vol_ratio = df['volume'] / avg_vol
        signals.loc[buy_cond | sell_cond, 'score'] = np.clip(vol_ratio * 10, 0, 100)[buy_cond | sell_cond]
        return signals

class BollingerBounceStrategy(BaseStrategy):
    def __init__(self, window: int = 20, num_std: float = 2.0):
        super().__init__(name=f"BBounce_{window}", window=window, num_std=num_std)
        self.window = window
        self.num_std = num_std

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signals = pd.DataFrame(index=df.index, columns=['decision', 'score']).fillna(0)
        ma = df['close'].rolling(window=self.window).mean()
        std = df['close'].rolling(window=self.window).std()
        upper = ma + (std * self.num_std)
        lower = ma - (std * self.num_std)
        
        buy_cond = (df['close'].shift(1) < lower.shift(1)) & (df['close'] > lower)
        sell_cond = (df['close'].shift(1) > upper.shift(1)) & (df['close'] < upper)
        
        signals.loc[buy_cond, 'decision'] = 1
        signals.loc[sell_cond, 'decision'] = -1
        signals.loc[buy_cond | sell_cond, 'score'] = 90.0
        return signals

class DisparityReversionStrategy(BaseStrategy):
    def __init__(self, window: int = 20, lower_bound: float = 0.90, upper_bound: float = 1.10):
        super().__init__(name=f"Disparity_{window}", window=window)
        self.window = window
        self.lower_bound = lower_bound
        self.upper_bound = upper_bound

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signals = pd.DataFrame(index=df.index, columns=['decision', 'score']).fillna(0)
        ma = df['close'].rolling(window=self.window).mean()
        disparity = df['close'] / ma
        
        buy_cond = disparity < self.lower_bound
        sell_cond = disparity > self.upper_bound
        
        signals.loc[buy_cond, 'decision'] = 1
        signals.loc[sell_cond, 'decision'] = -1
        signals.loc[buy_cond | sell_cond, 'score'] = 70.0
        return signals

class MACDReversalStrategy(BaseStrategy):
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        super().__init__(name=f"MACD_{fast}_{slow}")
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signals = pd.DataFrame(index=df.index, columns=['decision', 'score']).fillna(0)
        ema_fast = df['close'].ewm(span=self.fast, adjust=False).mean()
        ema_slow = df['close'].ewm(span=self.slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        signal_line = macd.ewm(span=self.signal, adjust=False).mean()
        
        prev_macd = macd.shift(1)
        prev_signal = signal_line.shift(1)
        
        buy_cond = (prev_macd < prev_signal) & (macd > signal_line) & (macd < 0) # 0선 아래에서 골든크로스
        sell_cond = (prev_macd > prev_signal) & (macd < signal_line) & (macd > 0)
        
        signals.loc[buy_cond, 'decision'] = 1
        signals.loc[sell_cond, 'decision'] = -1
        signals.loc[buy_cond | sell_cond, 'score'] = 85.0
        return signals

class DojiReboundStrategy(BaseStrategy):
    def __init__(self):
        super().__init__(name="Doji_Rebound")

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signals = pd.DataFrame(index=df.index, columns=['decision', 'score']).fillna(0)
        
        # 3일 연속 음봉
        is_down_1 = df['close'].shift(1) < df['open'].shift(1)
        is_down_2 = df['close'].shift(2) < df['open'].shift(2)
        is_down_3 = df['close'].shift(3) < df['open'].shift(3)
        
        # 오늘 도지 캔들 (시가-종가 차이가 고가-저가 차이의 10% 이내)
        body = abs(df['close'] - df['open'])
        rng = df['high'] - df['low']
        is_doji = body <= (rng * 0.1)
        
        buy_cond = is_down_1 & is_down_2 & is_down_3 & is_doji
        
        signals.loc[buy_cond, 'decision'] = 1
        signals.loc[buy_cond, 'score'] = 75.0
        # 매도는 다른 전략에 맡김 (0 유지)
        return signals

class High52WeekBreakoutStrategy(BaseStrategy):
    def __init__(self):
        super().__init__(name="High_52W_Breakout")

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signals = pd.DataFrame(index=df.index, columns=['decision', 'score']).fillna(0)
        # 250일(약 1년) 고점
        high_52w = df['high'].shift(1).rolling(window=250).max()
        
        buy_cond = df['close'] > high_52w
        signals.loc[buy_cond, 'decision'] = 1
        signals.loc[buy_cond, 'score'] = 100.0
        return signals

class IntradayDojiVolumeStrategy(BaseStrategy):
    """
    단타(Day Trading) 전용 전략:
    3분봉/5분봉에서 3연속 음봉 후 거래량이 터진 도지 캔들이 나오면 매수.
    15시 20분에 모든 포지션을 강제 청산하여 오버나잇을 방지.
    """
    def __init__(self, vol_mult: float = 2.0):
        super().__init__(name=f"Intraday_DojiVol_{vol_mult}x")
        self.vol_mult = vol_mult

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        signals = pd.DataFrame(index=df.index, columns=['decision', 'score']).fillna(0)
        
        is_down_1 = df['close'].shift(1) < df['open'].shift(1)
        is_down_2 = df['close'].shift(2) < df['open'].shift(2)
        is_down_3 = df['close'].shift(3) < df['open'].shift(3)
        
        body = abs(df['close'] - df['open'])
        rng = df['high'] - df['low']
        is_doji = body <= (rng * 0.2) # 분봉은 노이즈가 심해 도지 기준을 20%로 약간 완화
        
        avg_vol = df['volume'].shift(1).rolling(window=20).mean()
        is_vol_spike = df['volume'] > (avg_vol * self.vol_mult)
        
        buy_cond = is_down_1 & is_down_2 & is_down_3 & is_doji & is_vol_spike
        
        signals.loc[buy_cond, 'decision'] = 1
        signals.loc[buy_cond, 'score'] = 100.0
        
        # 15시 20분(장마감 전) 강제 청산 로직
        if 'date' in df.columns:
            date_col = pd.to_datetime(df['date'])
            force_sell_cond = (date_col.dt.hour == 15) & (date_col.dt.minute >= 20)
            signals.loc[force_sell_cond, 'decision'] = -1
            signals.loc[force_sell_cond, 'score'] = 100.0
            
        return signals
