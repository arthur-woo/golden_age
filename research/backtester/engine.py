import pandas as pd
import numpy as np

class BacktestEngine:
    """
    Pandas Vectorization 기반의 초고속 백테스트 엔진입니다.
    단타(Day Trading)를 위해 슬리피지(Slippage)와 트레일링 스탑(Trailing Stop) 기능이 추가되었습니다.
    """
    def __init__(self, initial_capital: float = 10000000.0, fee_rate: float = 0.00015, tax_rate: float = 0.002, slippage: float = 0.0005, trailing_stop_pct: float = 0.015):
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.tax_rate = tax_rate
        self.slippage = slippage
        self.trailing_stop_pct = trailing_stop_pct
        
    def run(self, ohlcv: pd.DataFrame, combined_signals: pd.DataFrame) -> pd.DataFrame:
        df = ohlcv.copy()
        df['decision'] = combined_signals['decision']
        df['score'] = combined_signals['score']
        
        positions = np.zeros(len(df))
        cash = np.zeros(len(df))
        holdings = np.zeros(len(df))
        
        current_cash = self.initial_capital
        current_position = 0 
        
        entry_price = 0.0
        high_since_entry = 0.0
        
        closes = df['close'].values
        decisions = df['decision'].values
        
        for i in range(len(df)):
            close_price = closes[i]
            decision = decisions[i]
            
            # 트레일링 스탑 로직 (보유 중일 때 고점 대비 하락 체크)
            if current_position > 0:
                high_since_entry = max(high_since_entry, close_price)
                if close_price < high_since_entry * (1.0 - self.trailing_stop_pct):
                    decision = -1 # 손절/익절 청산 시그널 강제 발생
            
            # 매수
            if decision == 1 and current_position == 0:
                buy_amount = current_cash
                # 슬리피지: 살 때 현재가보다 비싸게 샀다고 가정
                execution_price = close_price * (1.0 + self.slippage)
                fee = buy_amount * self.fee_rate
                invest_amount = buy_amount - fee
                
                current_position = invest_amount / execution_price
                current_cash = 0
                entry_price = execution_price
                high_since_entry = execution_price
            
            # 매도
            elif decision == -1 and current_position > 0:
                # 슬리피지: 팔 때 현재가보다 싸게 팔았다고 가정
                execution_price = close_price * (1.0 - self.slippage)
                sell_amount = current_position * execution_price
                fee = sell_amount * self.fee_rate
                tax = sell_amount * self.tax_rate
                
                current_cash = sell_amount - fee - tax
                current_position = 0
                entry_price = 0.0
                high_since_entry = 0.0
                
            cash[i] = current_cash
            holdings[i] = current_position * close_price
            
        df['cash'] = cash
        df['holdings'] = holdings
        df['total_value'] = df['cash'] + df['holdings']
        df['daily_return'] = df['total_value'].pct_change().fillna(0)
        
        return df
