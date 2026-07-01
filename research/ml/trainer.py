import os
import pandas as pd
import lightgbm as lgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, roc_auc_score
import joblib

def train_model():
    dataset_path = os.path.join(os.path.dirname(__file__), 'ml_dataset.csv')
    if not os.path.exists(dataset_path):
        print("❌ 에러: ml_dataset.csv 파일을 찾을 수 없습니다.")
        return
        
    df = pd.read_csv(dataset_path)
    if len(df) < 50:
        print("⚠️ 데이터가 너무 적습니다. 학습 신뢰도가 떨어질 수 있습니다.")
        
    # Feature와 Label 분리
    # label과 profit 제외한 나머지 컬럼이 모두 feature (단 date 컬럼이 있다면 제외)
    exclude_cols = ['label', 'profit', 'date']
    features = [col for col in df.columns if col not in exclude_cols]
    
    X = df[features]
    y = df['label']
    
    # Train / Test 분리 (80% / 20%)
    # 시계열 특성을 감안해 섞지 않고(shuffle=False) 자르거나 무작위로 나눌 수 있음
    # 여기서는 샘플수가 적어 무작위 분할(shuffle=True) 사용
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
    
    print(f"✅ 데이터 로드 완료 (전체: {len(df)}건, Train: {len(X_train)}건, Test: {len(X_test)}건)")
    
    # LightGBM 학습
    model = lgb.LGBMClassifier(
        n_estimators=100,
        learning_rate=0.05,
        max_depth=4,
        random_state=42,
        class_weight='balanced'
    )
    
    model.fit(X_train, y_train)
    
    # Test 셋 예측 (확률)
    y_pred_prob = model.predict_proba(X_test)[:, 1]
    
    # 기본 성능 평가 (Threshold 0.5)
    y_pred = (y_pred_prob > 0.5).astype(int)
    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, zero_division=0)
    
    # 클래스가 한개만 있을수도 있는 작은 데이터셋 예외처리
    try:
        auc = roc_auc_score(y_test, y_pred_prob)
    except:
        auc = 0.5
        
    print(f"\n--- 기본 모델 성능 (Test 셋) ---")
    print(f"Accuracy:  {acc:.3f}")
    print(f"Precision: {prec:.3f} (이 확률로 이깁니다)")
    print(f"ROC-AUC:   {auc:.3f}")
    
    # 모델 저장
    model_path = os.path.join(os.path.dirname(__file__), 'lgbm_filter.pkl')
    joblib.dump(model, model_path)
    print(f"\n✅ 모델 저장 완료: {model_path}")
    
    # Feature Importance 출력
    importance = pd.DataFrame({
        'Feature': features,
        'Importance': model.feature_importances_
    }).sort_values(by='Importance', ascending=False)
    
    print("\n--- Feature Importance (어떤 지표가 가장 중요했나?) ---")
    print(importance.head(10).to_string(index=False))
    
if __name__ == "__main__":
    train_model()
