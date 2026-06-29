import pandas as pd
import numpy as np

def calculate_metrics(portfolio_df: pd.DataFrame) -> dict:
    """
    백테스트 결과(포트폴리오 가치)를 바탕으로 핵심 성과 지표(KPI)를 계산합니다.
    입력: 'total_value', 'daily_return' 컬럼이 포함된 DataFrame
    """
    if portfolio_df.empty or 'total_value' not in portfolio_df.columns:
        return {}
        
    initial_capital = portfolio_df['total_value'].iloc[0]
    final_capital = portfolio_df['total_value'].iloc[-1]
    
    # 1. 누적 수익률 (Cumulative Return)
    cumulative_return = (final_capital / initial_capital) - 1.0
    
    # 2. 일간 수익률 시리즈
    daily_returns = portfolio_df['daily_return']
    
    # 3. MDD (Maximum Drawdown)
    running_max = portfolio_df['total_value'].cummax()
    drawdown = (portfolio_df['total_value'] - running_max) / running_max
    mdd = drawdown.min()
    
    # 4. 샤프 지수 (Sharpe Ratio) - 무위험 이자율 2% 가정
    risk_free_rate = 0.02
    daily_rf = risk_free_rate / 252
    
    if daily_returns.std() == 0:
        sharpe_ratio = 0.0
    else:
        sharpe_ratio = np.sqrt(252) * (daily_returns.mean() - daily_rf) / daily_returns.std()
        
    # 5. 승률 (Win Rate) - 일간 기준 승률
    win_days = (daily_returns > 0).sum()
    loss_days = (daily_returns < 0).sum()
    total_trade_days = win_days + loss_days
    win_rate = win_days / total_trade_days if total_trade_days > 0 else 0.0
    
    # 6. Profit Factor (총 수익 / 총 손실)
    gross_profit = daily_returns[daily_returns > 0].sum()
    gross_loss = abs(daily_returns[daily_returns < 0].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    return {
        "Initial Capital": initial_capital,
        "Final Capital": final_capital,
        "Cumulative Return": cumulative_return,
        "MDD": mdd,
        "Sharpe Ratio": sharpe_ratio,
        "Win Rate": win_rate,
        "Profit Factor": profit_factor
    }
