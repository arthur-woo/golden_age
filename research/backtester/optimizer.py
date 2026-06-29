import os
import sys
import glob
import pandas as pd
import numpy as np
import itertools
from tqdm import tqdm

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from research.strategies.sample_strategies import (
    MovingAverageBreakoutStrategy, RSIStrategy, MomentumBreakoutStrategy, 
    MAGoldenCrossStrategy, VolumeSpikeStrategy, BollingerBounceStrategy,
    DisparityReversionStrategy, MACDReversalStrategy, DojiReboundStrategy,
    High52WeekBreakoutStrategy
)
from research.backtester.engine import BacktestEngine
from research.backtester.metrics import calculate_metrics

def generate_weight_combinations(n: int, step: float = 0.1):
    """
    합이 1.0이 되는 n개 변수의 가중치 조합을 생성합니다.
    (예: n=3, step=0.1 -> (0.1, 0.1, 0.8), (0.1, 0.2, 0.7) ... 단, 0은 제외)
    """
    if n == 1:
        return [(1.0,)]
    
    values = np.arange(step, 1.0, step)
    valid_combos = []
    for combo in itertools.product(values, repeat=n):
        if abs(sum(combo) - 1.0) < 1e-5:
            valid_combos.append(tuple(round(w, 2) for w in combo))
    return valid_combos

def combine_signals_precomputed(df_len: int, sig_dfs: list, weights: tuple, threshold: float = 20.0) -> pd.DataFrame:
    mixed = pd.DataFrame(index=range(df_len))
    mixed_score = np.zeros(df_len)
    
    for sig_df, w in zip(sig_dfs, weights):
        dec_vals = sig_df['decision'].astype(float).fillna(0).values
        score_vals = sig_df['score'].astype(float).fillna(0).values
        mixed_score += (dec_vals * score_vals * w)
        
    mixed['mixed_raw_score'] = mixed_score
    mixed['decision'] = 0
    mixed['score'] = abs(mixed_score)
    
    mixed.loc[mixed['mixed_raw_score'] > threshold, 'decision'] = 1
    mixed.loc[mixed['mixed_raw_score'] < -threshold, 'decision'] = -1
    
    return mixed

def run_massive_grid_search():
    data_dir = os.path.join(os.path.dirname(__file__), '../data/csv_data')
    csv_files = glob.glob(os.path.join(data_dir, "*.csv"))
    
    if not csv_files:
        print("❌ 에러: csv_data 폴더에 데이터가 없습니다. 먼저 다운로드 스크립트를 실행해주세요.")
        return

    print(f"1. Loading Data for {len(csv_files)} stocks...")
    dfs = {}
    for file in csv_files:
        symbol = os.path.basename(file).split('.')[0]
        df = pd.read_csv(file)
        if len(df) > 50: # 너무 짧은 데이터는 제외
            dfs[symbol] = df
            
    print(f"Successfully loaded {len(dfs)} valid stocks.")

    print("2. Initializing 10 Core Strategies...")
    strategies = [
        MomentumBreakoutStrategy(window=20),
        MAGoldenCrossStrategy(short_win=5, long_win=20),
        VolumeSpikeStrategy(window=20, multiplier=3.0),
        BollingerBounceStrategy(window=20, num_std=2.0),
        DisparityReversionStrategy(window=20, lower_bound=0.9, upper_bound=1.1),
        MACDReversalStrategy(fast=12, slow=26, signal=9),
        DojiReboundStrategy(),
        High52WeekBreakoutStrategy(),
        MovingAverageBreakoutStrategy(window=20),
        RSIStrategy(window=14, oversold=30, overbought=70)
    ]
    
    # Pre-compute signals for all stocks and all strategies
    print("3. Pre-computing signals for all stocks (Caching in memory)...")
    precomputed = {} # precomputed[symbol][strategy_name] = sig_df
    
    for symbol, df in tqdm(dfs.items(), desc="Pre-computing"):
        precomputed[symbol] = {}
        for stg in strategies:
            precomputed[symbol][stg.name] = stg.generate_signals(df)

    print("4. Generating Combination Space (1~3 strategies)...")
    # 전략의 이름 리스트
    stg_names = [stg.name for stg in strategies]
    
    all_combos = []
    # 1개 선택
    for combo in itertools.combinations(stg_names, 1):
        for w in generate_weight_combinations(1):
            all_combos.append((combo, w))
            
    # 2개 선택
    for combo in itertools.combinations(stg_names, 2):
        for w in generate_weight_combinations(2, step=0.2): # 0.2 단위로 해서 경우의 수를 약간 조절
            all_combos.append((combo, w))
            
    # 3개 선택
    for combo in itertools.combinations(stg_names, 3):
        for w in generate_weight_combinations(3, step=0.3): # 0.3 단위로 조절
            all_combos.append((combo, w))

    print(f"Total Configurations to Test: {len(all_combos)}")
    
    engine = BacktestEngine(initial_capital=10000000.0)
    
    print("5. Running Massive Grid Search across all stocks...")
    results = []
    
    for combo_tuple, weights_tuple in tqdm(all_combos, desc="Optimizing"):
        # 각 종목별 지표를 모을 리스트
        combo_returns = []
        combo_mdds = []
        combo_sharpes = []
        combo_winrates = []
        combo_profits = []
        
        for symbol, df in dfs.items():
            # 이 종목의 선택된 전략 시그널들을 가져옴
            sig_dfs = [precomputed[symbol][s_name] for s_name in combo_tuple]
            
            # 믹스
            mixed = combine_signals_precomputed(len(df), sig_dfs, weights_tuple, threshold=20.0)
            
            # 백테스트
            bt_result = engine.run(df, mixed)
            
            # 성과 지표
            metrics = calculate_metrics(bt_result)
            if metrics:
                combo_returns.append(metrics.get('Cumulative Return', 0))
                combo_mdds.append(metrics.get('MDD', 0))
                combo_sharpes.append(metrics.get('Sharpe Ratio', 0))
                combo_winrates.append(metrics.get('Win Rate', 0))
                
                pf = metrics.get('Profit Factor', 1.0)
                if pf != float('inf'):
                    combo_profits.append(pf)
                    
        # 평균 산출
        if combo_returns:
            avg_return = np.mean(combo_returns) * 100
            avg_mdd = np.mean(combo_mdds) * 100
            avg_sharpe = np.mean(combo_sharpes)
            avg_winrate = np.mean(combo_winrates)
            avg_profit = np.mean(combo_profits) if combo_profits else 1.0
            
            # 조합 이름을 보기 좋게 문자열로
            combo_str = " + ".join([f"{s}({w})" for s, w in zip(combo_tuple, weights_tuple)])
            
            results.append({
                "Strategy Combination": combo_str,
                "Avg Return(%)": round(avg_return, 2),
                "Avg MDD(%)": round(avg_mdd, 2),
                "Avg Sharpe": round(avg_sharpe, 2),
                "Avg Win Rate": round(avg_winrate, 2),
                "Avg Profit Factor": round(avg_profit, 2)
            })

    print("\n--- Massive Grid Search Results (Top 15) ---")
    df_results = pd.DataFrame(results)
    df_results = df_results.sort_values(by=["Avg Return(%)", "Avg Sharpe"], ascending=[False, False]).reset_index(drop=True)
    
    print(df_results.head(15).to_string())
    
    # 결과를 CSV로 저장
    res_path = os.path.join(os.path.dirname(__file__), '../data/optimization_results.csv')
    df_results.to_csv(res_path, index=False)
    print(f"\n✅ 전체 결과가 {res_path} 에 저장되었습니다.")

if __name__ == "__main__":
    run_massive_grid_search()
