# 운영 커맨드 런북 (사용자 실행용)

이 문서는 **사용자가 직접 실행해야 하는** 커맨드를 순서대로 정리한다.
모든 커맨드는 `backend/`에서 실행한다: `cd backend`.
`<ID>`는 대상 `Account`의 id로 바꾼다.

> [!IMPORTANT]
> 실제 데이터/주문이 흐르려면 **유효한 KIS 실키 + 장중(KST 09:00~15:30) 실행**이 필요하다.
> 코드는 "할 수 있게" 만들어져 있을 뿐, **아무 것도 자동으로 켜져 있지 않다.**

---

## 0. 사전 준비

```bash
# DB(Postgres)·Docker 기동 확인 (docker desktop 등)
# 마이그레이션
python manage.py migrate

# 관리자 계정 (Admin UI 접근용, 선택)
python manage.py createsuperuser
```

**Account 등록**: Admin UI(`/admin/`) 또는 shell로 `acc_account`에 발급받은 KIS 정보 입력.
- `broker=KIS`, `account_type=PAPER`(먼저) 또는 `LIVE`
- `account_number` = 10자리(CANO 8 + 상품코드 2, 예: `5012345601`)
- `app_key_encrypted` / `app_secret_encrypted` = 발급받은 appkey / appsecret **원문**(현재 평문 저장)

---

## 1. KIS 실계정 검증 (반드시 먼저, PAPER)

```bash
# 1-1) 인증 + 시세 스모크 (키/URL 유효성)
python manage.py shell -c "
from apps.account.models import Account
from apps.account.services import get_broker_for_account
b = get_broker_for_account(Account.objects.get(id=<ID>))
print('price', b.get_current_price('005930'))
print('balance', b.get_balance())
"

# 1-2) 분봉 백필 (REST 수집 검증)
python manage.py backfill_candles --account-id <ID> --symbol 005930 --pages 3

# 1-3) 실시간 수집 (★장중에만★, Ctrl+C 종료)
python manage.py collect_realtime --symbol 005930 --account-id <ID>

# 1-4) 소액 주문 (hashkey 정상 접수 확인)
python manage.py shell -c "
from decimal import Decimal
from apps.account.models import Account
from apps.account.services import get_broker_for_account
b = get_broker_for_account(Account.objects.get(id=<ID>))
r = b.create_order('005930','BUY',Decimal('1'))
print('success', r.success, 'odno', r.order_id, 'msg', r.error_message)
"

# 1-5) 체결 확인
python manage.py shell -c "
from apps.account.models import Account
from apps.account.services import get_broker_for_account
b = get_broker_for_account(Account.objects.get(id=<ID>))
print(b.get_order_execution())
"
```

**실패 시**: 1-1 오류 → 키·계좌번호·PAPER/LIVE URL 불일치. 1-4 `rt_cd≠0` → `msg1` 확인(hashkey/필드명 이슈면 알려줄 것).

---

## 2. 데이터 준비

```bash
# 2-1) (선택) 연구용 CSV 일봉 임포트 — 백테스트/개발용
python manage.py import_csv_candles --dir ../research/data/csv_data

# 2-2) 수집 유니버스(COLLECTION) 구성 — 매매와 분리된 넓은 상위집합, append-only
python manage.py sync_collection_universe --all-with-candles
#   또는 특정 종목만:
python manage.py sync_collection_universe --symbols 005930 000660 035420

# 2-3) 매매 유니버스(KOSPI200 등) 리밸런싱 — 편입/편출 이력 유지(생존편향 제거)
python manage.py import_universe_membership --universe KOSPI200 --file kospi200.txt
#   또는: --symbols 005930 000660 ...
```

---

## 3. 실시간·백필 수집 (핵심)

```bash
# 3-1) hot set 실시간 수집 (장중 상주) — 미래 1분봉 누적의 주력
#      hot set이 세션 한도(~40) 초과 시 자동으로 다중 WS 세션 분산 구독
python manage.py collect_realtime --universe --top 80 --account-id <ID>

# 3-2) broad 백필 자동화 (무인) — 나머지·구멍 메우기 (당일~최근)
python manage.py register_backfill_schedule --account-id <ID> --minutes 5 --pages 2

# 3-3) django_q 워커 가동 (스케줄 실행 주체) — 별도 터미널에 상주
python manage.py qcluster
```

> **역할 분담**: 3-1(실시간 WS)=긴 히스토리를 매일 누적 / 3-2(REST 배치)=당일 보조·broad 커버리지.
> REST는 당일~최근만 주므로 **깊은 과거 1분봉은 3-1로 매일 쌓아야** 확보된다.

---

## 4. 거래 파이프라인 실행

```bash
# 4-1) 수동 1회 실행
python manage.py run_account_pipeline <ID>

# 4-2) 분 단위 자동 스케줄 등록 (qcluster 가동 필요)
python manage.py register_schedule --account-id <ID> --minutes 1
```

> 실거래 전 반드시 **PAPER**로 검증. `Trader.config_payload`로 사이징/리스크/청산 조정:
> `{"advanced_sizing": true, "eod_flatten": true, "risk_limits": {"max_position_ratio": 0.15, "daily_loss_limit_ratio": 0.03}}`
> (`eod_flatten`=종가 강제청산, `daily_loss_limit_ratio`=일일손실 킬스위치)

---

## 4-2. 안전 감시·정산 (상주/주기 실행 권장)

```bash
# 상시 손절/익절 감시 — 분 파이프라인보다 자주(예: 15~30초) 스케줄
python manage.py monitor_risk <ID> --stop 0.05 --take 0.1

# 원장 리컨실리에이션 — 브로커 실보유 ↔ 내부 원장 대조/보정 (재시작·불일치 시)
python manage.py reconcile_account <ID>            # 대조만
python manage.py reconcile_account <ID> --apply    # 브로커 기준 보정

# 헬스체크 — 데이터 신선도·미체결·마지막 실행 상태 (이상 시 exit 1 → 알림 연동)
python manage.py health_check <ID>
```

> 지수 레짐(B-5)을 쓰려면 `settings.REGIME_INDEX_SYMBOL = "069500"`(예: KODEX200) 설정 +
> 그 종목을 수집/백필 대상에 포함. CHAOS 레짐이면 신규 진입이 자동 차단된다.

---

## 5. 모델 학습·배포

```bash
# 데이터셋 생성 -> LightGBM 학습 -> 배포 (mkt_candle 데이터 필요)
python manage.py train_daytrading_model --symbol 005930 --model-version v1 --deploy \
    --upper 0.004 --lower 0.004 --horizon 10
```

배포되면 `Trader.ml_filter_enabled=True`인 트레이더가 자동으로 실 모델을 사용(없으면 규칙기반 폴백).

---

## 6. 참고: 백테스트/분석

측정용 스크립트는 `scratchpad/`에 ad-hoc으로 두고 트랜잭션 롤백으로 돌렸다(정식 커맨드 아님).
정식화가 필요하면 `analyze_strategy` 관리 커맨드로 승격 요청할 것.
- 다봉 백테스트: `core.backtest.universe_runner.run_universe_backtest`
- 과최적화 진단: `core.ml.diagnostics.pbo_cscv`, `deflated_sharpe_ratio`
- Walk-Forward: `core.ml.wfv.run_dataset_walk_forward`

---

## 7. 주의사항

- **아무 것도 자동 상주 아님**: `collect_realtime`, `qcluster`는 사람이 띄워 유지해야 한다. 상주 배포는 [docs/deploy/README.md](deploy/README.md)의 systemd/supervisor 예시 참고.
- **장 시간**: KST 09:00~15:30 밖에는 실시간 체결이 없다.
- **보안**: `app_key`/`app_secret`가 현재 **평문 저장**. LIVE 전 암호화 필요.
- **수집 구멍 방지**: 수집은 COLLECTION 상위집합에 **append-only**(편출돼도 계속). 매매만 시점별 필터.
- **정직한 현실**: 현재 룰베이스 전략은 백테스트에서 비용을 못 이겼고(과최적 PBO 높음), ML도 일봉 프록시에선 신호가 미약했다. **실 1분봉 누적 후 재검증**이 전제.
