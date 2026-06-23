# Database Schema

Golden Age의 데이터베이스는 개인용 주식 거래 봇을 오래 유지보수하기 위한 구조를 목표로 한다.

너무 복잡한 플랫폼 구조를 만들지 않고, 두 명의 개발자가 각자 Strategy를 독립적으로 만들고 최적화할 수 있는 최소한의 경계를 둔다.

---

# 1. 도메인 Prefix

테이블 이름은 도메인 구분을 위해 2~4자 prefix를 붙인다.

| Prefix | Domain | 설명 |
| --- | --- | --- |
| acc | Account | 계좌, 잔고, 계좌 실행 |
| trd | Trading | Trader, Strategy, 매매 판단 |
| mkt | Market | 시장 국면, 시세, Feature |
| stk | Stock | 종목 |
| ord | Order | 주문, 체결 |
| sys | System | 모델, 학습 데이터, 시스템 산출물 |

예시

- `acc_account`
- `trd_trader`
- `mkt_candle`
- `stk_stock`
- `ord_order`
- `sys_model_artifact`

---

# 2. 공통 규칙

## Primary Key

모든 업무 테이블의 기본 키는 `bigint`를 사용한다.

Django에서는 기본적으로 `BigAutoField`를 사용한다.

## 시간 컬럼

모든 시간 컬럼은 `timestamptz`를 사용한다.

기본 컬럼은 다음 이름을 사용한다.

- `created_at`
- `updated_at`
- `deleted_at`

이벤트성 테이블은 이벤트 시각을 별도로 가진다.

예시

- `executed_at`
- `decided_at`
- `analyzed_at`
- `trained_at`

## 금액과 수량

금액은 `numeric(18,2)`를 사용한다.

수량은 `numeric(18,8)`를 사용한다.

비율과 Score는 기본적으로 `numeric(10,6)`를 사용한다.

## JSONB 사용 기준

`jsonb`는 다음 경우에만 사용한다.

- 외부 API 원본 응답 보존
- Strategy별 설정값
- Feature Vector
- ML 모델 출력
- 재현을 위한 입력/출력 Snapshot

업무상 자주 필터링하는 값은 별도 컬럼으로 둔다.

---

# 3. 사용자와 계좌

## auth_user

Django 기본 `auth_user`를 사용한다.

별도의 User 모델은 만들지 않는다.

---

## acc_account

증권계좌와 Broker API 인증 정보를 함께 저장한다.

개인용 봇이고 계좌별 활성 인증 정보가 사실상 1개이므로 `broker_credential` 테이블을 따로 두지 않는다.

민감 정보는 애플리케이션 레이어에서 암호화하여 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| user_id | bigint fk | `auth_user.id` |
| broker | varchar(32) | `KIS` |
| account_type | varchar(16) | `LIVE`, `PAPER` |
| account_number | varchar(64) | 증권 계좌번호 |
| name | varchar(100) | 계좌 별칭 |
| investment_profile | varchar(32) | 투자 성향 |
| app_key_encrypted | text | 암호화된 App Key |
| app_secret_encrypted | text | 암호화된 App Secret |
| is_active | boolean | 사용 여부 |
| created_at | timestamptz | 생성 시각 |
| updated_at | timestamptz | 수정 시각 |
| deleted_at | timestamptz null | 비활성화 시각 |

제약

- `account_type in ('LIVE', 'PAPER')`
- `(user_id, broker, account_type, account_number)` unique

인덱스

- `(user_id, is_active)`
- `(broker, account_type)`

---

## acc_broker_token

Broker API 토큰을 저장한다.

토큰은 만료와 재발급 주기가 짧으므로 `acc_account`와 분리한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| account_id | bigint fk | `acc_account.id` |
| access_token_encrypted | text | 암호화된 Access Token |
| token_type | varchar(32) | 토큰 타입 |
| expires_at | timestamptz | 만료 시각 |
| issued_at | timestamptz | 발급 시각 |
| revoked_at | timestamptz null | 폐기 시각 |
| created_at | timestamptz | 생성 시각 |

인덱스

- `(account_id, expires_at)`
- `(revoked_at)`

---

# 4. 종목과 시장 데이터

## stk_stock

거래 가능한 종목을 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| market | varchar(32) | `KRX`, `KOSPI`, `KOSDAQ` 등 |
| symbol | varchar(32) | 종목 코드 |
| name | varchar(100) | 종목명 |
| currency | varchar(8) | `KRW` |
| is_active | boolean | 사용 여부 |
| created_at | timestamptz | 생성 시각 |
| updated_at | timestamptz | 수정 시각 |

제약

- `(market, symbol)` unique

인덱스

- `(symbol)`
- `(market, is_active)`

---

## mkt_candle

OHLCV 데이터를 저장한다.

Append-only를 기본으로 한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| stock_id | bigint fk | `stk_stock.id` |
| timeframe | varchar(16) | `1m`, `5m`, `15m`, `1d` |
| opened_at | timestamptz | 캔들 시작 시각 |
| open_price | numeric(18,2) | 시가 |
| high_price | numeric(18,2) | 고가 |
| low_price | numeric(18,2) | 저가 |
| close_price | numeric(18,2) | 종가 |
| volume | numeric(18,8) | 거래량 |
| source | varchar(32) | 데이터 출처 |
| raw_payload | jsonb | 원본 응답 |
| created_at | timestamptz | 저장 시각 |

제약

- `(stock_id, timeframe, opened_at, source)` unique

인덱스

- `(stock_id, timeframe, opened_at desc)`

---

## mkt_regime_snapshot

Market Analyzer의 시장 국면 판단 결과를 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| stock_id | bigint fk null | 특정 종목 기준일 때 사용 |
| regime | varchar(16) | `BULL`, `SIDEWAYS`, `BEAR` |
| confidence_score | numeric(10,6) | 판단 신뢰도 |
| parameter_payload | jsonb | Trader/Strategy에 전달할 시장 파라미터 |
| reason | text | 판단 근거 |
| analyzed_at | timestamptz | 분석 시각 |
| created_at | timestamptz | 저장 시각 |

제약

- `regime in ('BULL', 'SIDEWAYS', 'BEAR')`

인덱스

- `(stock_id, analyzed_at desc)`
- `(regime, analyzed_at desc)`

---

## mkt_feature_snapshot

Strategy와 ML Filter가 사용한 입력 Feature를 저장한다.

매매 판단 재현을 위한 Source of Truth 중 하나다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| stock_id | bigint fk | `stk_stock.id` |
| timeframe | varchar(16) | Feature 기준 timeframe |
| feature_payload | jsonb | Feature Vector |
| source_payload | jsonb | 원천 데이터 참조 또는 요약 |
| captured_at | timestamptz | Feature 기준 시각 |
| created_at | timestamptz | 저장 시각 |

인덱스

- `(stock_id, timeframe, captured_at desc)`

---

# 5. Trader와 Strategy

## trd_trader

자동매매 봇을 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| account_id | bigint fk | `acc_account.id` |
| name | varchar(100) | Trader 이름 |
| code | varchar(64) | 계좌 내 고유 코드 |
| status | varchar(16) | `ACTIVE`, `PAUSED`, `STOPPED` |
| position_size_ratio | numeric(10,6) | 기본 포지션 비율 |
| entry_threshold | numeric(10,6) | 기본 진입 기준 |
| stop_loss_ratio | numeric(10,6) | 손절 기준 |
| take_profit_ratio | numeric(10,6) | 익절 기준 |
| max_exposure_ratio | numeric(10,6) | 최대 노출 비율 |
| ml_filter_enabled | boolean | ML Filter 사용 여부 |
| config_payload | jsonb | Trader별 추가 설정 |
| created_at | timestamptz | 생성 시각 |
| updated_at | timestamptz | 수정 시각 |
| deleted_at | timestamptz null | 비활성화 시각 |

제약

- `(account_id, code)` unique
- `status in ('ACTIVE', 'PAUSED', 'STOPPED')`

인덱스

- `(account_id, status)`

---

## trd_strategy

Strategy의 논리적 식별자를 저장한다.

Strategy 코드는 데이터베이스에 저장하지 않는다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| owner_id | bigint fk | `auth_user.id` |
| namespace | varchar(64) | 개발자 또는 전략 네임스페이스 |
| name | varchar(100) | Strategy 이름 |
| code | varchar(64) | Strategy 고유 코드 |
| description | text | 설명 |
| is_active | boolean | 사용 여부 |
| created_at | timestamptz | 생성 시각 |
| updated_at | timestamptz | 수정 시각 |
| deleted_at | timestamptz null | 비활성화 시각 |

제약

- `(namespace, code)` unique

인덱스

- `(owner_id, is_active)`
- `(namespace, is_active)`

---

## trd_strategy_version

Strategy 구현 버전을 저장한다.

개발자별 Strategy 변경이 다른 Strategy에 영향을 주지 않도록 Strategy 실행 단위는 Version으로 고정한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| strategy_id | bigint fk | `trd_strategy.id` |
| version | varchar(32) | 사람이 읽는 버전 |
| module_path | varchar(255) | Python module path |
| class_name | varchar(100) | Strategy class name |
| commit_hash | varchar(64) null | 코드 커밋 식별자 |
| config_schema | jsonb | 설정 스키마 |
| default_config | jsonb | 기본 설정 |
| status | varchar(16) | `DRAFT`, `ACTIVE`, `RETIRED` |
| created_at | timestamptz | 생성 시각 |
| retired_at | timestamptz null | 종료 시각 |

제약

- `(strategy_id, version)` unique
- `status in ('DRAFT', 'ACTIVE', 'RETIRED')`

인덱스

- `(strategy_id, status)`

---

## trd_trader_strategy

Trader와 Strategy Version의 연결을 저장한다.

Trader는 최대 2개의 활성 Strategy를 가진다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| trader_id | bigint fk | `trd_trader.id` |
| strategy_version_id | bigint fk | `trd_strategy_version.id` |
| slot | smallint | `1`, `2` |
| weight | numeric(10,6) | Score 조합 가중치 |
| config_payload | jsonb | Trader에 연결된 Strategy 설정 |
| is_active | boolean | 사용 여부 |
| created_at | timestamptz | 생성 시각 |
| updated_at | timestamptz | 수정 시각 |
| deleted_at | timestamptz null | 비활성화 시각 |

제약

- `slot in (1, 2)`
- `weight >= 0`
- 활성 상태에서 `(trader_id, slot)` unique

인덱스

- partial unique index: `(trader_id, slot) where is_active = true`
- `(strategy_version_id, is_active)`

---

# 6. 실행과 판단 로그

## acc_execution_run

Scheduler가 Account 단위로 실행한 결과를 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| account_id | bigint fk | `acc_account.id` |
| run_type | varchar(32) | `SCHEDULED`, `MANUAL` |
| status | varchar(16) | `RUNNING`, `SUCCESS`, `FAILED` |
| started_at | timestamptz | 시작 시각 |
| finished_at | timestamptz null | 종료 시각 |
| error_message | text null | 실패 사유 |
| created_at | timestamptz | 생성 시각 |

인덱스

- `(account_id, started_at desc)`
- `(status, started_at desc)`

---

## trd_execution_run

Account 실행 중 Trader별 실행 결과를 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| account_execution_run_id | bigint fk | `acc_execution_run.id` |
| trader_id | bigint fk | `trd_trader.id` |
| stock_id | bigint fk | `stk_stock.id` |
| market_regime_snapshot_id | bigint fk null | `mkt_regime_snapshot.id` |
| status | varchar(16) | `RUNNING`, `SUCCESS`, `FAILED`, `SKIPPED` |
| started_at | timestamptz | 시작 시각 |
| finished_at | timestamptz null | 종료 시각 |
| error_message | text null | 실패 사유 |
| created_at | timestamptz | 생성 시각 |

인덱스

- `(trader_id, started_at desc)`
- `(stock_id, started_at desc)`

---

## trd_strategy_decision_log

각 Strategy의 판단 결과를 append-only로 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| trader_execution_run_id | bigint fk | `trd_execution_run.id` |
| trader_strategy_id | bigint fk | `trd_trader_strategy.id` |
| strategy_version_id | bigint fk | `trd_strategy_version.id` |
| feature_snapshot_id | bigint fk | `mkt_feature_snapshot.id` |
| decision | varchar(16) | `BUY`, `SELL`, `HOLD` |
| score | numeric(10,6) | Strategy Score |
| reason | text | 판단 근거 |
| input_payload | jsonb | Strategy 입력 Snapshot |
| output_payload | jsonb | Strategy 출력 Snapshot |
| decided_at | timestamptz | 판단 시각 |
| created_at | timestamptz | 저장 시각 |

제약

- `decision in ('BUY', 'SELL', 'HOLD')`
- `score >= 0`

인덱스

- `(trader_execution_run_id)`
- `(strategy_version_id, decided_at desc)`
- `(decision, decided_at desc)`

---

## trd_ml_output_log

ML Filter의 판단 보조 결과를 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| trader_execution_run_id | bigint fk | `trd_execution_run.id` |
| model_artifact_id | bigint fk null | `sys_model_artifact.id` |
| trade_probability | numeric(10,6) | 거래 성공 가능성 |
| risk_score | numeric(10,6) | 리스크 점수 |
| expected_return | numeric(10,6) | 기대 수익 |
| input_payload | jsonb | ML 입력 Snapshot |
| output_payload | jsonb | ML 출력 원본 |
| created_at | timestamptz | 생성 시각 |

인덱스

- `(trader_execution_run_id)`
- `(model_artifact_id, created_at desc)`

---

## trd_decision_log

Trader의 최종 매매 방향을 저장한다.

Strategy Score와 ML Output을 조합한 최종 결과다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| trader_execution_run_id | bigint fk | `trd_execution_run.id` |
| ml_output_log_id | bigint fk null | `trd_ml_output_log.id` |
| final_decision | varchar(16) | `BUY`, `SELL`, `HOLD` |
| final_score | numeric(10,6) | 최종 Score |
| position_size_ratio | numeric(10,6) | 적용 포지션 비율 |
| stop_loss_ratio | numeric(10,6) | 적용 손절 기준 |
| take_profit_ratio | numeric(10,6) | 적용 익절 기준 |
| max_exposure_ratio | numeric(10,6) | 적용 최대 노출 비율 |
| reason | text | 최종 판단 근거 |
| decision_payload | jsonb | 최종 판단 Snapshot |
| decided_at | timestamptz | 판단 시각 |
| created_at | timestamptz | 저장 시각 |

제약

- `final_decision in ('BUY', 'SELL', 'HOLD')`

인덱스

- `(trader_execution_run_id)`
- `(final_decision, decided_at desc)`

---

# 7. 주문과 체결

## ord_order

Broker에 전달한 주문 요청과 현재 주문 상태를 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| trader_decision_log_id | bigint fk | `trd_decision_log.id` |
| account_id | bigint fk | `acc_account.id` |
| stock_id | bigint fk | `stk_stock.id` |
| side | varchar(8) | `BUY`, `SELL` |
| order_type | varchar(16) | `MARKET`, `LIMIT` |
| quantity | numeric(18,8) | 주문 수량 |
| limit_price | numeric(18,2) null | 지정가 |
| status | varchar(32) | 현재 주문 상태 |
| broker_order_id | varchar(100) null | Broker 주문번호 |
| request_payload | jsonb | 요청 원본 |
| response_payload | jsonb | 응답 원본 |
| requested_at | timestamptz | 요청 시각 |
| created_at | timestamptz | 저장 시각 |
| updated_at | timestamptz | 수정 시각 |

제약

- `side in ('BUY', 'SELL')`
- `order_type in ('MARKET', 'LIMIT')`

인덱스

- `(account_id, requested_at desc)`
- `(stock_id, requested_at desc)`
- `(broker_order_id)`
- `(status, requested_at desc)`

---

## ord_order_event

주문 상태 변경 이벤트를 append-only로 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| order_id | bigint fk | `ord_order.id` |
| event_type | varchar(32) | `CREATED`, `ACCEPTED`, `PARTIALLY_FILLED`, `FILLED`, `CANCELED`, `REJECTED` |
| broker_status | varchar(64) | Broker 원본 상태 |
| event_payload | jsonb | 원본 이벤트 |
| occurred_at | timestamptz | 이벤트 발생 시각 |
| created_at | timestamptz | 저장 시각 |

인덱스

- `(order_id, occurred_at)`
- `(event_type, occurred_at desc)`

---

## ord_trade_execution

실제 체결 결과를 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| order_id | bigint fk | `ord_order.id` |
| account_id | bigint fk | `acc_account.id` |
| stock_id | bigint fk | `stk_stock.id` |
| side | varchar(8) | `BUY`, `SELL` |
| executed_quantity | numeric(18,8) | 체결 수량 |
| executed_price | numeric(18,2) | 체결 가격 |
| fee_amount | numeric(18,2) | 수수료 |
| tax_amount | numeric(18,2) | 세금 |
| slippage_amount | numeric(18,2) | 슬리피지 |
| broker_execution_id | varchar(100) null | Broker 체결번호 |
| executed_at | timestamptz | 체결 시각 |
| created_at | timestamptz | 저장 시각 |

인덱스

- `(account_id, executed_at desc)`
- `(stock_id, executed_at desc)`
- `(order_id)`
- `(broker_execution_id)`

---

# 8. 잔고와 포지션

## acc_cash_ledger

현금 변동 이력을 append-only로 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| account_id | bigint fk | `acc_account.id` |
| trade_execution_id | bigint fk null | `ord_trade_execution.id` |
| event_type | varchar(32) | `DEPOSIT`, `WITHDRAWAL`, `BUY`, `SELL`, `FEE`, `TAX`, `ADJUSTMENT` |
| amount | numeric(18,2) | 증감 금액 |
| currency | varchar(8) | `KRW` |
| reason | text | 사유 |
| occurred_at | timestamptz | 발생 시각 |
| created_at | timestamptz | 저장 시각 |

인덱스

- `(account_id, occurred_at desc)`
- `(trade_execution_id)`

---

## acc_position_ledger

보유 수량 변동 이력을 append-only로 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| account_id | bigint fk | `acc_account.id` |
| stock_id | bigint fk | `stk_stock.id` |
| trade_execution_id | bigint fk null | `ord_trade_execution.id` |
| quantity_delta | numeric(18,8) | 수량 증감 |
| price | numeric(18,2) | 기준 가격 |
| reason | text | 사유 |
| occurred_at | timestamptz | 발생 시각 |
| created_at | timestamptz | 저장 시각 |

인덱스

- `(account_id, stock_id, occurred_at desc)`
- `(trade_execution_id)`

---

## acc_balance_snapshot

조회 성능을 위한 계좌 잔고 Snapshot이다.

원본 데이터는 `acc_cash_ledger`, `acc_position_ledger`다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| account_id | bigint fk | `acc_account.id` |
| cash_balance | numeric(18,2) | 현금 잔고 |
| total_asset_value | numeric(18,2) | 총 평가 금액 |
| snapshot_payload | jsonb | 상세 Snapshot |
| snapshotted_at | timestamptz | Snapshot 시각 |
| created_at | timestamptz | 저장 시각 |

인덱스

- `(account_id, snapshotted_at desc)`

---

# 9. ML 학습 파이프라인

ML 관련 테이블은 시스템 산출물로 보고 `sys` prefix를 사용한다.

## sys_model_artifact

학습된 모델 산출물을 저장한다.

실제 모델 파일은 파일 스토리지에 저장하고, 데이터베이스에는 메타데이터만 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| model_name | varchar(100) | 모델 이름 |
| version | varchar(32) | 모델 버전 |
| artifact_uri | text | 모델 파일 위치 |
| artifact_checksum | varchar(128) | 파일 checksum |
| training_dataset_id | bigint fk null | `sys_training_dataset.id` |
| metrics_payload | jsonb | 학습/검증 지표 |
| status | varchar(16) | `TRAINING`, `READY`, `DEPLOYED`, `RETIRED` |
| trained_at | timestamptz null | 학습 완료 시각 |
| created_at | timestamptz | 생성 시각 |
| retired_at | timestamptz null | 종료 시각 |

제약

- `(model_name, version)` unique

인덱스

- `(status, trained_at desc)`

---

## sys_model_deployment

모델 배포 이력을 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| model_artifact_id | bigint fk | `sys_model_artifact.id` |
| trader_id | bigint fk null | `trd_trader.id` |
| status | varchar(16) | `ACTIVE`, `RETIRED` |
| deployed_at | timestamptz | 배포 시각 |
| retired_at | timestamptz null | 종료 시각 |
| created_at | timestamptz | 생성 시각 |

인덱스

- `(trader_id, status)`
- `(model_artifact_id, status)`

---

## sys_training_dataset

학습 데이터셋 버전을 저장한다.

Dataset은 생성 후 수정하지 않는다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| name | varchar(100) | Dataset 이름 |
| version | varchar(32) | Dataset 버전 |
| source_started_at | timestamptz | 학습 데이터 시작 시각 |
| source_ended_at | timestamptz | 학습 데이터 종료 시각 |
| feature_definition | jsonb | Feature 정의 |
| label_definition | jsonb | Label 정의 |
| status | varchar(16) | `BUILDING`, `READY`, `RETIRED` |
| created_at | timestamptz | 생성 시각 |
| completed_at | timestamptz null | 생성 완료 시각 |

제약

- `(name, version)` unique

인덱스

- `(status, created_at desc)`

---

## sys_training_dataset_item

Dataset에 포함된 개별 학습 샘플을 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| id | bigint pk | 내부 식별자 |
| training_dataset_id | bigint fk | `sys_training_dataset.id` |
| trader_decision_log_id | bigint fk | `trd_decision_log.id` |
| trade_execution_id | bigint fk null | `ord_trade_execution.id` |
| feature_snapshot_id | bigint fk | `mkt_feature_snapshot.id` |
| label | smallint | `1` profit, `0` loss |
| realized_return | numeric(10,6) | 실현 수익률 |
| feature_payload | jsonb | 학습용 Feature |
| label_payload | jsonb | Label 생성 근거 |
| created_at | timestamptz | 생성 시각 |

제약

- `(training_dataset_id, trader_decision_log_id)` unique
- `label in (0, 1)`

인덱스

- `(training_dataset_id)`
- `(label)`

---

# 10. 핵심 관계 요약

```text
auth_user
  ↓
acc_account
  ↓
trd_trader
  ↓
trd_trader_strategy
  ↓
trd_strategy_version
  ↓
trd_strategy
```

```text
acc_execution_run
  ↓
trd_execution_run
  ↓
trd_strategy_decision_log
  ↓
trd_ml_output_log
  ↓
trd_decision_log
  ↓
ord_order
  ↓
ord_order_event
  ↓
ord_trade_execution
```

```text
mkt_feature_snapshot
  ↓
trd_strategy_decision_log
  ↓
trd_decision_log
  ↓
sys_training_dataset_item
  ↓
sys_training_dataset
  ↓
sys_model_artifact
  ↓
sys_model_deployment
```

---

# 11. MVP 우선순위

## Phase 1 필수

- `acc_account`
- `acc_broker_token`
- `stk_stock`
- `trd_trader`
- `trd_strategy`
- `trd_strategy_version`
- `trd_trader_strategy`

## Phase 2 필수

- `acc_execution_run`
- `trd_execution_run`
- `mkt_candle`
- `mkt_regime_snapshot`
- `mkt_feature_snapshot`
- `trd_strategy_decision_log`
- `trd_decision_log`

## Phase 3 필수

- `ord_order`
- `ord_order_event`
- `ord_trade_execution`
- `acc_cash_ledger`
- `acc_position_ledger`
- `acc_balance_snapshot`

## Phase 4 이후

- `trd_ml_output_log`
- `sys_training_dataset`
- `sys_training_dataset_item`
- `sys_model_artifact`
- `sys_model_deployment`

---

# 12. 구현 메모

- 외부 API 호출은 트랜잭션 밖에서 수행한다.
- 주문 요청 저장과 주문 이벤트 저장은 짧은 트랜잭션으로 처리한다.
- Ledger와 Log 테이블은 update하지 않는다.
- 현재 상태 조회가 필요하면 Snapshot 테이블을 별도로 둔다.
- Strategy 실행은 `trd_strategy_version` 기준으로 고정한다.
- Trader당 Strategy 수 제한은 `trd_trader_strategy.slot in (1, 2)`로 단순하게 표현한다.
- 개발자별 Strategy 격리는 `trd_strategy.owner_id`, `trd_strategy.namespace`, `trd_strategy_version.module_path`로 추적한다.
- 계좌 인증 정보는 `acc_account`에 둔다.
- 토큰은 만료/재발급 이력이 있어 `acc_broker_token`으로 분리한다.
