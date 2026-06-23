# Golden Age

> Personal Automated Trading Platform for Korea Investment Open API

---

# 1. Project Overview

Golden Age는 개인 투자자를 위한 주식 자동매매 플랫폼이다.

이 프로젝트의 목표는 단순한 자동 주문 프로그램이 아니라, **증권계좌(Account)를 중심으로 여러 개의 Trader와 Strategy를 독립적으로 운용할 수 있는 자동매매 시스템**을 구축하는 것이다.

초기 목표는 **한국투자증권 모의투자를 이용하여 실제 자동매매가 가능한 수준까지 구현**하는 것이다.

1차 목표는 기본적으로 사용할 수 있는 자동매매 구조를 만들고, 개발자 2명이 각자 자신의 생각에 맞는 Strategy를 독립적으로 생성하고 최적화할 수 있는 기반을 제공하는 것이다.

프로젝트는 유지보수성과 확장성을 고려하여 설계하지만, 과도한 추상화는 지양한다.

---

# 2. Goals

## Primary Goals

- 한국투자증권 Open API 연동
- 모의투자 자동매매 구현
- 실계좌 자동매매 지원
- 여러 개의 증권계좌 운영
- 계좌별 독립적인 Trader 운용
- Trader별 최대 2개 Strategy 운용
- Strategy 스코어링 기반 최종 매매 방향 결정
- 개발자별 Strategy 코드 격리
- LightGBM 기반 ML Filter 연동
- Feature Store, Dataset Builder, Training Pipeline 기반 학습 데이터 축적

## Non Goals

초기 버전에서는 아래 기능은 구현하지 않는다.

- 백테스트
- AI 전략 생성
- 차트 분석 UI
- 모바일 앱
- 다중 증권사 지원

ML은 Strategy를 생성하지 않는다.

ML은 사람이 정의한 Strategy의 판단을 필터링하고 보조한다.

---

# 3. Tech Stack

## Backend

- Python
- Django
- Django Admin

## Database

- PostgreSQL

## Scheduler

- Django Q2

## Broker

- Korea Investment Open API

## Infrastructure

Docker Container

- postgres
- web
- scheduler

---

# 4. Core Concepts

## User

사용자는 Django 기본 `auth_user`를 사용한다.

별도의 User 모델은 만들지 않는다.

관계

```
User

↓

Account
```

---

## Account

Account는 로그인 계정(Account)이 아니라 **증권계좌**를 의미한다.

예)

- 한국투자증권 실계좌
- 한국투자증권 모의계좌

하나의 User는 여러 개의 Account를 가질 수 있다.

예)

```
Master

├── 공격형 실계좌
├── 중립형 실계좌
└── 전략 테스트 모의계좌
```

Account는 다음 정보를 가진다.

- API 인증 정보
- 계좌번호
- 투자 성향
- Trader 목록

Account는 자동매매의 실행 단위이다.

---

## Trader

Trader는 하나의 자동매매 봇이다.

하나의 Account에는 여러 개의 Trader를 등록할 수 있다.

예)

```
VWAP Trader

Swing Trader

Breakout Trader
```

Trader는 최대 2개의 Strategy를 가진다.

Trader는 각 Strategy의 판단 결과와 Score를 수집하고, 이를 조합하여 최종 매매 방향을 결정한다.

Trader는 다음 책임을 가진다.

- Strategy 실행
- Strategy별 Score 수집
- 최종 Buy/Sell/Hold 결정
- 포지션 크기 결정
- 손절/익절 기준 적용
- 최대 노출 비율 관리
- ML Filter 호출

---

## Strategy

Strategy는 실제 매매 판단을 수행한다.

Strategy는 시장을 판단하지 않는다.

Strategy는

- Buy
- Sell
- Hold
- Score

만 결정한다.

Strategy는 재사용 가능해야 한다.

Strategy는 다음 책임을 가지지 않는다.

- 포트폴리오 관리
- 자금 배분
- 리스크 관리
- 시장 국면 판단
- 다른 Strategy 결과 해석

개발자별 Strategy는 서로 독립적으로 유지한다.

한 개발자가 자신의 Strategy를 변경하더라도 다른 개발자의 Strategy 동작에 영향을 주면 안 된다.

---

## Market

시장 상태는 세 가지만 사용한다.

- BULL
- SIDEWAYS
- BEAR

Market Analyzer가 현재 시장을 판단한다.

Trader는 판단 결과를 이용하여 Strategy를 교체하지 않는다.

Market Analyzer는 시장 국면에 따라 Trader와 Strategy에 전달할 파라미터를 조정한다.

---

# 5. Trading Flow

```
Scheduler

↓

Account

↓

Market Analyzer

↓

Trader

↓

Strategy A / Strategy B

↓

ML Filter

↓

Trading Service

↓

Broker

↓

Korea Investment Open API
```

---

# 6. Scheduler

모든 배치는 반드시 **Account 단위**로 실행한다.

예)

```
09:00

↓

Account A 실행

↓

Account B 실행

↓

Account C 실행
```

각 Account는 서로 영향을 주지 않는다.

---

# 7. Account Types

지원하는 계좌

- LIVE
- PAPER

Broker는 Account Type에 따라 자동으로 API를 선택한다.

---

# 8. Trader Rules

모든 Trader는 Strategy를 최대 2개 가진다.

Strategy가 1개인 Trader는 해당 Strategy의 결과와 Score를 기준으로 최종 결정을 내린다.

Strategy가 2개인 Trader는 두 Strategy의 결과와 Score를 조합하여 최종 결정을 내린다.

예)

```
Momentum Trader

Strategy A
↓
Momentum Strategy

Strategy B
↓
Volume Confirmation Strategy

Scores
↓
Trader Decision
```

Trader는 시장 국면별 Strategy를 교체하지 않는다.

시장 국면은 Strategy 선택이 아니라 파라미터 조정과 Score 해석에 사용한다.

---

# 9. Development Principles

- Service Layer를 사용한다.
- Model에는 Business Logic를 넣지 않는다.
- Strategy Pattern을 사용한다.
- Broker Interface를 사용한다.
- 모든 주문은 추적 가능해야 한다.
- 모든 거래는 재현 가능해야 한다.
- 모든 전략 판단은 로그를 남긴다.
- 개발자별 Strategy 구현은 서로 격리한다.
- 공통 인터페이스 변경은 신중하게 진행한다.

---

# 10. Project Structure

```
golden_age/

README.md

AGENTS.md

docs/

docs/database.md

docs/db_schema.md

backend/

docker/
```

---

# 11. Development Roadmap

Phase 1

- 프로젝트 생성
- Docker
- Django
- PostgreSQL
- Database 설계
- Django Admin

Phase 2

- 한국투자 Open API
- Broker 구현
- Token 관리

Phase 3

- Scheduler
- Market Analyzer
- Trader
- Strategy

Phase 4

- 개발자별 Strategy 구현
- Strategy Scoring
- ML Filter
- 모의투자 자동매매

Phase 5

- 실계좌 자동매매
- 운영 및 안정화
- Dataset Builder
- Model Training Pipeline

---

# 12. Philosophy

Golden Age는 "복잡한 시스템"을 만드는 것이 목적이 아니다.

빠르게 자동매매를 실행하고, 실제 운용을 통해 지속적으로 개선하는 것을 목표로 한다.

필요한 만큼만 설계하고, 단순하고 이해하기 쉬운 구조를 유지한다.

---

# 13. Data Collection (Market Data Layer)

Golden Age는 모든 매매 판단 이전에 시장 데이터를 수집하고 저장하는 Data Collection Layer를 가진다.

## Data Sources

### Market Data

- 현재가
- 캔들 (1m / 5m / 15m / 1d)
- 거래량
- 호가
- 체결강도

### Execution Data

- 주문 생성 시점
- 체결 여부
- 체결 가격
- 슬리피지

### Context Data

- Market State
- Trader ID
- Strategy ID
- Account ID
- Strategy Score
- ML Output

## Storage

```
MarketData
OrderEvent
TradeExecution
StrategyDecisionLog
FeatureSnapshot
MLOutputLog
TrainingDataset
```

---

# 14. Feature Engineering Layer

Raw 데이터는 직접 사용하지 않고 Feature로 변환한다.

## Pipeline

```
Market Data
↓
Normalizer
↓
Feature Builder
↓
Feature Store
↓
ML Input Dataset
```

## Features

### Price

- return_1m
- return_5m
- return_20m
- gap_open

### Volume

- volume_ratio
- volume_spike
- trade_intensity

### Trend

- MA(5, 20, 60)
- VWAP deviation
- momentum

### Volatility

- ATR
- price_std

### Context

- market_state
- trader_id
- strategy_id

---

# 15. Machine Learning Layer (LightGBM)

Strategy를 대체하지 않고 판단을 보조하는 Layer이다.

## Model

LightGBM

## Role

- 매매 신호 필터링
- 리스크 평가
- 기대 수익 보정

ML Filter는 Strategy와 Trader의 책임을 침범하지 않는다.

최종 주문 여부는 Trader가 결정한다.

## Output

```python
trade_probability
risk_score
expected_return
```

## Flow

```
Strategy Scores
↓
ML Filter
↓
Trader Final Decision
↓
Execution
```

---

# 16. Training Dataset Construction

## Structure

```
X = Feature Vector
Y = Trade Result
```

## Label

- 1 = profit
- 0 = loss

## Flow

```
Trade Execution
↓
Result Analysis
↓
Dataset 생성
↓
Batch Training
```

---

# 17. Feedback Learning Loop

```
Execution
↓
Evaluation
↓
Dataset Update
↓
Model Training
↓
Deployment
```

- 1일 1회 retrain
- 주 1회 full retrain

Django Q2 기반 batch job 사용

---

# 18. ML Integration Point

```
Market Analyzer
↓
Trader
↓
Strategy A / Strategy B
↓
Strategy Scores
↓
ML Filter
↓
Trader Final Decision
↓
Trading Service
↓
Broker
```

---

# 19. Logging & Traceability

- Feature Snapshot
- Strategy Decision
- ML Output
- Final Decision
- Execution Result
- Training Dataset Version
- Model Version

---

# 20. Philosophy Update

- 전략은 사람이 정의한다
- ML은 판단을 보조한다
- 모든 거래는 데이터가 된다
- 모든 데이터는 학습에 사용된다
- 개발자별 Strategy는 독립적으로 발전한다
- 시스템은 시간이 지날수록 개선된다

---

# 21. Final Architecture

```
Scheduler
↓
Account Executor
↓
Market Data Collector
↓
Feature Builder
↓
Market Analyzer
↓
Trader
↓
Strategy A / Strategy B
↓
Strategy Scoring
↓
ML Filter (LightGBM)
↓
Trader Final Decision
↓
Trading Service
↓
Broker API
↓
Execution Logger
↓
Dataset Builder
↓
Model Training Pipeline
```

---

# Summary

Golden Age = Independent Rule-based Strategies + Strategy Scoring + LightGBM Filtering + Continuous Learning System
