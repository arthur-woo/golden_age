import os
import sys
import glob
import pandas as pd
import numpy as np
import itertools
from tqdm import tqdm

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from research.strategies.sample_strategies import IntradayDojiVolumeStrategy, MovingAverageBreakoutStrategy, RSIStrategy
from research.backtester.engine import BacktestEngine
from research.backtester.metrics import calculate_metrics

def generate_weight_combinations(n: int, step: float = 0.1):
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
    
    # 15시 20분 이후는 모든 시그널을 -1로 강제 청산 (오버나잇 금지)
    # sig_dfs 중 하나에서 강제 청산 시그널(-1)이 나오면 무조건 -1로 덮어씌움
    force_sell_mask = np.zeros(df_len, dtype=bool)
    for sig_df in sig_dfs:
        force_sell_mask |= (sig_df['decision'].values == -1) & (sig_df['score'].values == 100.0)
        
    mixed.loc[force_sell_mask, 'decision'] = -1
    
    return mixed

def run_intraday_grid_search():
    data_dir = os.path.join(os.path.dirname(__file__), '../data/intraday_csv')
    csv_files = glob.glob(os.path.join(data_dir, "*.csv"))
    
    if not csv_files:
        print("❌ 에러: intraday_csv 폴더에 데이터가 없습니다.")
        return

    print(f"1. Loading Intraday Data for {len(csv_files)} stocks...")
    dfs = {}
    for file in csv_files:
        symbol = os.path.basename(file).split('_')[0]
        df = pd.read_csv(file)
        if len(df) > 50:
            dfs[symbol] = df
            
    print(f"Successfully loaded {len(dfs)} valid stocks.")

    print("2. Initializing Intraday Strategies...")
    # 단타용 전략들을 다양하게 세팅
    strategies = [
        IntradayDojiVolumeStrategy(vol_mult=1.5),
        IntradayDojiVolumeStrategy(vol_mult=2.0),
        IntradayDojiVolumeStrategy(vol_mult=3.0),
        MovingAverageBreakoutStrategy(window=20),
        MovingAverageBreakoutStrategy(window=60),
        RSIStrategy(window=14, oversold=30, overbought=70)
    ]
    
    print("3. Pre-computing signals for all stocks (Caching in memory)...")
    precomputed = {}
    
    for symbol, df in tqdm(dfs.items(), desc="Pre-computing"):
        precomputed[symbol] = {}
        for stg in strategies:
            precomputed[symbol][stg.name] = stg.generate_signals(df)

    print("4. Generating Combination Space (1~2 strategies)...")
    stg_names = [stg.name for stg in strategies]
    
    all_combos = []
    # 1개 선택
    for combo in itertools.combinations(stg_names, 1):
        for w in generate_weight_combinations(1):
            all_combos.append((combo, w))
            
    # 2개 선택
    for combo in itertools.combinations(stg_names, 2):
        for w in generate_weight_combinations(2, step=0.2):
            all_combos.append((combo, w))

    print(f"Total Configurations to Test: {len(all_combos)}")
    
    # 단타용 엔진 설정 (슬리피지 0.05%, 트레일링 스탑 1.5%)
    engine = BacktestEngine(initial_capital=10000000.0, slippage=0.0005, trailing_stop_pct=0.015)
    
    print("5. Running Intraday Grid Search across all stocks...")
    results = []
    
    for combo_tuple, weights_tuple in tqdm(all_combos, desc="Optimizing"):
        combo_returns = []
        combo_mdds = []
        combo_sharpes = []
        combo_winrates = []
        
        for symbol, df in dfs.items():
            sig_dfs = [precomputed[symbol][s_name] for s_name in combo_tuple]
            mixed = combine_signals_precomputed(len(df), sig_dfs, weights_tuple, threshold=20.0)
            bt_result = engine.run(df, mixed)
            metrics = calculate_metrics(bt_result)
            
            if metrics:
                combo_returns.append(metrics.get('Cumulative Return', 0))
                combo_mdds.append(metrics.get('MDD', 0))
                combo_sharpes.append(metrics.get('Sharpe Ratio', 0))
                combo_winrates.append(metrics.get('Win Rate', 0))
                
        if combo_returns:
            avg_return = np.mean(combo_returns) * 100
            avg_mdd = np.mean(combo_mdds) * 100
            avg_sharpe = np.mean(combo_sharpes)
            avg_winrate = np.mean(combo_winrates)
            
            combo_str = " + ".join([f"{s}({w})" for s, w in zip(combo_tuple, weights_tuple)])
            
            results.append({
                "Strategy Combination": combo_str,
                "Avg Return(%)": round(avg_return, 2),
                "Avg MDD(%)": round(avg_mdd, 2),
                "Avg Sharpe": round(avg_sharpe, 2),
                "Avg Win Rate": round(avg_winrate, 2),
            })

    print("\n--- Intraday Grid Search Results (Top 10) ---")
    df_results = pd.DataFrame(results)
    df_results = df_results.sort_values(by=["Avg Return(%)", "Avg Sharpe"], ascending=[False, False]).reset_index(drop=True)
    
    print(df_results.head(10).to_string())
    
    res_path = os.path.join(os.path.dirname(__file__), '../data/intraday_optimization_results.csv')
    df_results.to_csv(res_path, index=False)
    print(f"\n✅ 단타 최적화 결과가 {res_path} 에 저장되었습니다.")

if __name__ == "__main__":
    run_intraday_grid_search()
