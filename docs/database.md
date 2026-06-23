# Database

상세 테이블 스키마는 [db_schema.md](db_schema.md)를 기준으로 한다.

## 목표

Golden Age의 데이터베이스는 다음 원칙을 따른다.

- 데이터 무결성을 최우선으로 한다.
- 읽기보다 쓰기의 정확성을 우선한다.
- 변경 이력을 최대한 보존한다.
- 비즈니스 로직은 가능한 애플리케이션에서 처리한다.
- 데이터베이스는 데이터 저장과 조회에 집중한다.

---

# 설계 원칙

## 1. 정규화 우선

중복 데이터는 최소화한다.

성능 때문에 비정규화를 적용해야 하는 경우에는 명확한 근거가 있어야 한다.

---

## 2. 삭제보다 상태 변경

가능하면 물리 삭제(Delete)를 하지 않는다.

상태(Status) 변경이나 종료 시각을 기록하여 이력을 보존한다.

예외

- 임시 데이터
- 캐시
- 세션
- 로그성 데이터

---

## 3. 모든 중요한 데이터는 이력을 남긴다.

예시

- 주문 상태
- 포인트
- 잔고
- 보유 수량
- 전략 변경
- 설정 변경
- 전략 판단
- Strategy Score
- ML 판단 결과
- 학습 데이터셋 생성 이력
- 모델 배포 이력

현재 상태(Current)는 조회를 위한 데이터이며,

변경 이력(Ledger)이 실제 데이터의 원본(Source of Truth)이다.

---

# ID 정책

기본 키는 bigint(BIGSERIAL 또는 Identity)를 사용한다.

int4는 충분하지 않다.

UUID는 외부 공개 식별자가 필요한 경우에만 사용한다.

---

# 시간 관리

모든 시간은 UTC 기준으로 저장한다.

애플리케이션에서 사용자 시간대로 변환한다.

모든 시간 컬럼은 timestamptz를 사용한다.

예시

- created_at
- updated_at
- deleted_at
- executed_at

---

# 금액

실수(float)는 사용하지 않는다.

모든 금액은 decimal(NUMERIC)을 사용한다.

예시

NUMERIC(18,2)

수량이 필요한 경우

NUMERIC(18,8)

---

# 인덱스

인덱스는 조회 패턴에 맞게 생성한다.

원칙

- Primary Key
- Foreign Key
- 자주 조회되는 조건
- 정렬 조건

사용하지 않는 인덱스는 제거한다.

---

# 트랜잭션

트랜잭션은 최대한 짧게 유지한다.

외부 API 호출은 트랜잭션 안에서 수행하지 않는다.

잠금(Lock)을 오래 유지하지 않는다.

---

# 포인트 및 잔고

포인트와 잔고는 Ledger 방식으로 관리한다.

현재 잔액은 집계 결과이거나 별도의 Summary 테이블에서 관리한다.

Ledger는 절대 수정하지 않는다.

잘못 기록된 경우에는 반대 거래를 추가한다.

---

# 주문

주문 상태는 변경될 수 있다.

하지만 주문 이벤트는 변경되지 않는다.

예시

Order

- 주문 정보

Order Event

- 주문 생성
- 접수
- 체결
- 부분 체결
- 취소

---

# Strategy 데이터

개발자별 Strategy 구현은 서로 독립적이어야 한다.

데이터베이스도 Strategy의 소유와 변경 이력을 추적할 수 있어야 한다.

예시

- trd_strategy
- trd_strategy_version
- trd_trader_strategy
- trd_strategy_decision_log

Strategy Decision Log는 매매 판단 재현을 위해 append-only로 관리한다.

기록해야 하는 정보

- account_id
- trader_id
- strategy_id
- strategy_version_id
- input_snapshot_id
- decision
- score
- reason
- decided_at

Strategy 코드는 데이터베이스에 저장하지 않는다.

데이터베이스에는 Strategy 식별자, 버전, 설정, 판단 결과만 저장한다.

---

# ML 데이터

ML Filter는 Strategy를 대체하지 않고 판단을 보조한다.

Feature, Dataset, Model, ML Output은 모두 추적 가능해야 한다.

예시

- mkt_feature_snapshot
- trd_ml_output_log
- sys_training_dataset
- sys_training_dataset_item
- sys_model_artifact
- sys_model_deployment

Feature Snapshot은 매매 판단 시점의 입력을 재현할 수 있어야 한다.

ML Output Log는 다음 정보를 기록한다.

- trader_id
- strategy_decision_log_id
- model_artifact_id
- trade_probability
- risk_score
- expected_return
- output_payload
- created_at

Training Dataset은 버전 단위로 관리한다.

이미 생성된 Dataset은 수정하지 않는다.

잘못 생성된 Dataset은 새 버전을 생성하여 대체한다.

---

# 시세 데이터

OHLCV 데이터는 Append Only 방식으로 저장한다.

과거 데이터는 수정하지 않는다.

필요 시 재수집하여 교체한다.

---

# 로그

애플리케이션 로그와 비즈니스 데이터는 분리한다.

대량 로그는 일정 기간 이후 Archive 또는 삭제한다.

---

# Archive 정책

운영 데이터와 Archive 데이터를 분리한다.

예시

- acc_cash_ledger
- acc_cash_ledger_archive

Archive는 연도 또는 월 단위 파티셔닝을 고려한다.

운영 테이블은 가능한 작게 유지한다.

---

# 명명 규칙

테이블

도메인 prefix를 붙인 snake_case

예시

acc_account

trd_trader

mkt_candle

stk_stock

ord_order

sys_model_artifact

컬럼

snake_case

Foreign Key

xxx_id

예시

account_id

order_id

strategy_id

시간 컬럼

- created_at
- updated_at
- deleted_at

Boolean

is_

예시

is_active

is_deleted

---

# 성능 원칙

- SELECT * 사용을 지양한다.
- 필요한 컬럼만 조회한다.
- N+1 문제를 방지한다.
- 큰 OFFSET 사용을 지양한다.
- 가능하면 Keyset Pagination을 사용한다.
- 실행 계획(EXPLAIN ANALYZE)을 통해 성능을 검증한다.

---

# PostgreSQL

Golden Age는 PostgreSQL을 기본 데이터베이스로 사용한다.

PostgreSQL의 장점을 적극 활용한다.

- JSONB
- Partial Index
- Expression Index
- Window Function
- CTE
- Partitioning
- UPSERT
- Materialized View (필요한 경우)

단, PostgreSQL 전용 기능은 유지보수성과 성능을 고려하여 사용한다.

---

# 목표

데이터베이스는 단순하고, 안정적이며, 장기간 운영해도 성능이 유지되는 구조를 지향한다.
