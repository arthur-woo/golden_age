import os
import pandas as pd
import joblib

def evaluate_thresholds():
    dataset_path = os.path.join(os.path.dirname(__file__), 'ml_dataset.csv')
    model_path = os.path.join(os.path.dirname(__file__), 'lgbm_filter.pkl')
    
    if not os.path.exists(dataset_path) or not os.path.exists(model_path):
        print("❌ Dataset or Model not found.")
        return
        
    df = pd.read_csv(dataset_path)
    model = joblib.load(model_path)
    
    exclude_cols = ['label', 'profit', 'date']
    features = [col for col in df.columns if col not in exclude_cols]
    
    X = df[features]
    
    # 1. 원본 (Baseline) 성과
    # 원래 룰 기반 전략이 다 매수했을 때의 성과
    baseline_trades = len(df)
    baseline_profit = df['profit'].sum()
    baseline_win_rate = df['label'].mean() * 100
    
    # 확률 예측
    df['pred_prob'] = model.predict_proba(X)[:, 1]
    
    print(f"📊 --- ML Filter 성과 비교 리포트 (총 {baseline_trades}건의 시그널 발생) ---")
    print(f"[0] Baseline (No ML Filter)")
    print(f"  - 거래 횟수: {baseline_trades}회")
    print(f"  - 승률: {baseline_win_rate:.1f}%")
    print(f"  - 누적 수익금: {baseline_profit:,.0f}원\n")
    
    thresholds = [0.4, 0.5, 0.6, 0.7]
    for th in thresholds:
        # 필터링 통과한 거래만 추림
        filtered_df = df[df['pred_prob'] >= th]
        
        trades = len(filtered_df)
        if trades == 0:
            print(f"[ML] Threshold {th:.2f}: 거래 발생 안함")
            continue
            
        profit = filtered_df['profit'].sum()
        win_rate = filtered_df['label'].mean() * 100
        
        print(f"[ML] Threshold {th:.2f} 적용 시")
        print(f"  - 거래 횟수: {trades}회 ({(trades/baseline_trades)*100:.1f}% 필터링 통과)")
        print(f"  - 승률: {win_rate:.1f}%")
        print(f"  - 누적 수익금: {profit:,.0f}원\n")

if __name__ == "__main__":
    evaluate_thresholds()
