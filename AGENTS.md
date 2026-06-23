# AGENTS.md

## 프로젝트

Golden Age는 개인용 알고리즘 주식 자동매매 플랫폼입니다.

최우선 목표는 단기적인 성능 극대화가 아니라 장기적으로 유지보수하기 쉬운 구조를 만드는 것입니다.

1차 목표는 기본적으로 운용 가능한 자동매매 구조를 만들고, 개발자 2명이 각자 자신의 전략을 독립적으로 생성하고 최적화할 수 있는 기반을 제공하는 것입니다.

---

## 아키텍처 원칙

- 아키텍처는 최대한 단순하게 유지한다.
- 상속보다 조합(Composition)을 우선한다.
- 암묵적인 동작보다 명시적인 코드를 선호한다.
- 모듈 간 결합도를 최소화한다.
- 모든 컴포넌트는 하나의 책임만 가진다.

---

## 트레이딩 철학

### Trader

하나의 Trader는 최대 2개의 Strategy를 가진다.

Trader는 Strategy 결과를 단순히 선택하지 않고, 각 Strategy의 판단 결과를 스코어링하여 최종 매매 방향을 결정한다.

Trader의 역할은 다음 책임을 가진다.

- Strategy 실행 순서 제어
- Strategy별 Score 수집
- 최종 매매 방향 결정
- 포지션 크기(Position Size) 결정
- 손절 기준(Stop Loss) 적용
- 익절 기준(Take Profit) 적용
- 최대 노출 비율(Maximum Exposure) 적용

시장 상황에 따라 Strategy를 교체하지 않는다.

대신 Market Analyzer가 다음과 같은 전략 파라미터를 조정한다.

- 포지션 크기(Position Size)
- 진입 기준(Entry Threshold)
- 손절 기준(Stop Loss)
- 익절 기준(Take Profit)
- 최대 노출 비율(Maximum Exposure)

### Strategy

Strategy는 항상 동일한 입력에 동일한 결과를 반환하는 결정적(Deterministic)이어야 한다.

Strategy의 역할은 다음 판단과 점수를 반환하는 것이다.

- 매수(Buy)
- 매도(Sell)
- 관망(Hold)
- 신뢰도 또는 강도 Score

Strategy는 다음 사항을 알거나 처리해서는 안 된다.

- 포트폴리오
- 자금 배분
- 리스크 관리
- 시장 국면(Market Regime)
- 다른 Strategy의 결과

이러한 책임은 모두 Trader가 담당한다.

개발자별 Strategy 코드는 서로 영향을 주지 않아야 한다.

- 공통 인터페이스와 입력/출력 DTO는 공유한다.
- Strategy 내부 구현은 독립적으로 유지한다.
- 한 개발자의 Strategy 변경이 다른 개발자의 Strategy 동작을 바꾸면 안 된다.
- 공통 로직 변경이 필요한 경우 Strategy별 영향 범위를 먼저 확인한다.

---

## Market Analyzer

Market Analyzer는 현재 시장의 국면(Market Regime)을 분석한다.

시장 상태는 다음 세 가지로 구분한다.

- 상승장(BULL)
- 횡보장(SIDEWAYS)
- 하락장(BEAR)

Market Analyzer의 역할은 전략을 선택하는 것이 아니라 Trader와 Strategy에 전달할 시장 파라미터를 조정하는 것이다.

---

## Machine Learning

LightGBM 기반 ML Filter는 Strategy를 대체하지 않는다.

ML의 역할은 Rule-based Strategy와 Trader의 결정을 보조하는 것이다.

- 매매 신호 필터링
- 리스크 평가
- 기대 수익 보정

Feature Store, Dataset Builder, Training Pipeline은 장기적인 개선을 위한 데이터 파이프라인이다.

초기 구현에서는 ML 모델 성능보다 다음 항목을 우선한다.

- 매매 판단 재현성
- Feature Snapshot 저장
- Strategy Decision 저장
- ML Output 저장
- Execution Result 저장

---

## 개발 가이드라인

- 함수는 가능한 한 작게 작성한다.
- 성급한 최적화를 피한다.
- 성능보다 읽기 쉬운 코드를 우선한다.
- 타입 안정성을 적극 활용한다.
- 가능한 경우 트레이딩 로직에 대한 테스트를 작성한다.
- 개발자별 Strategy 경계를 침범하지 않는다.
- 공통 모듈은 작고 안정적인 인터페이스를 우선한다.

---

## AI Assistant 가이드라인

이 저장소를 수정할 때는 다음 원칙을 따른다.

1. 기존 아키텍처를 유지한다.
2. 불필요한 추상화를 추가하지 않는다.
3. 비즈니스 로직과 인프라 코드를 명확히 분리한다.
4. 큰 구조 변경이 필요한 경우 먼저 변경 이유를 설명한다.
5. 대규모 리팩터링보다 점진적인 개선을 우선한다.

---

## 목표

이해하기 쉽고, 확장하기 쉬우며, 장기간 안정적으로 유지보수할 수 있는 자동매매 플랫폼을 구축한다.
