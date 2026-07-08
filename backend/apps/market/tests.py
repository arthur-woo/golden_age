from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from core.market.aggregator import Tick, OHLCV, aggregate_ticks, floor_to_minute
from core.features.builder import build_features
from core.ml.labeling import triple_barrier_label, UPPER, LOWER, TIME


def _candle(close, high=None, low=None, open_=None, volume=1000, opened_at=None):
    close = float(close)
    return SimpleNamespace(
        close_price=Decimal(str(close)),
        high_price=Decimal(str(high if high is not None else close)),
        low_price=Decimal(str(low if low is not None else close)),
        open_price=Decimal(str(open_ if open_ is not None else close)),
        volume=Decimal(str(volume)),
        opened_at=opened_at,
    )


def _tick(h, m, s, price, vol):
    return Tick(
        ts=datetime(2024, 1, 2, h, m, s, tzinfo=dt_timezone.utc),
        price=Decimal(str(price)),
        volume=Decimal(str(vol)),
    )


class AggregatorTestCase(SimpleTestCase):
    """1분봉 집계 순수 함수 검증 (DB 불필요)."""

    def test_floor_to_minute(self):
        ts = datetime(2024, 1, 2, 9, 3, 45, 123, tzinfo=dt_timezone.utc)
        self.assertEqual(
            floor_to_minute(ts),
            datetime(2024, 1, 2, 9, 3, 0, 0, tzinfo=dt_timezone.utc),
        )

    def test_empty(self):
        self.assertEqual(aggregate_ticks([]), [])

    def test_single_minute_ohlcv(self):
        ticks = [
            _tick(9, 0, 1, 100, 10),
            _tick(9, 0, 20, 105, 5),
            _tick(9, 0, 40, 98, 7),
            _tick(9, 0, 59, 102, 3),
        ]
        candles = aggregate_ticks(ticks)
        self.assertEqual(len(candles), 1)
        c = candles[0]
        self.assertEqual(c.open, Decimal("100"))
        self.assertEqual(c.high, Decimal("105"))
        self.assertEqual(c.low, Decimal("98"))
        self.assertEqual(c.close, Decimal("102"))
        self.assertEqual(c.volume, Decimal("25"))
        self.assertEqual(c.trade_count, 4)

    def test_multiple_minutes_sorted(self):
        ticks = [
            _tick(9, 1, 10, 200, 1),
            _tick(9, 0, 10, 100, 1),
            _tick(9, 0, 30, 110, 1),
            _tick(9, 2, 5, 300, 1),
        ]
        candles = aggregate_ticks(ticks)
        self.assertEqual([c.opened_at.minute for c in candles], [0, 1, 2])
        self.assertEqual(candles[0].open, Decimal("100"))
        self.assertEqual(candles[0].close, Decimal("110"))

    def test_unordered_input_is_deterministic(self):
        ticks = [
            _tick(9, 0, 40, 98, 1),
            _tick(9, 0, 1, 100, 1),
            _tick(9, 0, 59, 102, 1),
        ]
        c = aggregate_ticks(ticks)[0]
        # ts 기준 정렬되므로 open=첫 체결(9:00:01), close=마지막(9:00:59)
        self.assertEqual(c.open, Decimal("100"))
        self.assertEqual(c.close, Decimal("102"))


class FeatureBuilderTestCase(SimpleTestCase):
    """Feature 빌더 순수 함수 검증 (DB 불필요). 캔들은 최신 -> 과거 순."""

    def test_empty(self):
        self.assertEqual(build_features([]), {})

    def test_constant_price_gives_rsi_100(self):
        # Phase 4 ML 필터가 의존하는 성질: 종가 일정 -> RSI 100
        candles = [_candle(70500) for _ in range(20)]
        feats = build_features(candles, current_price=70000)
        self.assertEqual(feats["rsi"], 100.0)
        self.assertEqual(feats["close"], 70500.0)
        self.assertEqual(feats["current_price"], 70000.0)

    def test_uptrend_features(self):
        # 최신 -> 과거 순으로 감소값 = 시간순 상승
        candles = [_candle(100 - i) for i in range(25)]
        feats = build_features(candles)
        self.assertGreater(feats["sma_ratio"], 1.0)  # 단기 MA > 장기 MA
        self.assertGreater(feats["ret_5"], 0.0)  # 최근 5봉 상승
        self.assertEqual(feats["rsi"], 100.0)  # 지속 상승 -> RSI 100

    def test_deterministic(self):
        candles = [_candle(100 + (i % 3)) for i in range(30)]
        self.assertEqual(build_features(candles), build_features(candles))

    def test_time_features_present_when_opened_at(self):
        ts = datetime(2024, 1, 2, 9, 30, tzinfo=dt_timezone.utc)  # 개장 30분 후
        candles = [_candle(100, opened_at=ts)] + [_candle(100) for _ in range(20)]
        feats = build_features(candles)
        self.assertEqual(feats["minutes_since_open"], 30.0)


class TripleBarrierTestCase(SimpleTestCase):
    """Triple-Barrier 라벨러 검증. closes는 과거 -> 최신 순 float."""

    def test_upper_barrier_hit(self):
        # +1% 상승이 익절(upper=0.5%) 먼저 도달
        closes = [100.0, 100.2, 100.6, 101.0]
        label = triple_barrier_label(
            closes, entry_index=0, upper=0.005, lower=0.02, horizon=3
        )
        self.assertEqual(label.barrier, UPPER)
        self.assertEqual(label.label, 1)

    def test_lower_barrier_hit(self):
        closes = [100.0, 99.8, 99.0, 98.0]
        label = triple_barrier_label(
            closes, entry_index=0, upper=0.02, lower=0.005, horizon=3
        )
        self.assertEqual(label.barrier, LOWER)
        self.assertEqual(label.label, 0)

    def test_time_barrier(self):
        # 배리어 미도달 -> 시간 청산, 소폭 상승이라 label=1
        closes = [100.0, 100.05, 100.1, 100.15]
        label = triple_barrier_label(
            closes, entry_index=0, upper=0.02, lower=0.02, horizon=3
        )
        self.assertEqual(label.barrier, TIME)
        self.assertEqual(label.label, 1)

    def test_cost_flips_label_to_zero(self):
        # 총이익 0.15%인데 왕복비용 0.21% -> 순손실 -> label 0
        closes = [100.0, 100.05, 100.1, 100.15]
        label = triple_barrier_label(
            closes, entry_index=0, upper=0.02, lower=0.02, horizon=3, cost=0.0021
        )
        self.assertEqual(label.barrier, TIME)
        self.assertLess(label.net_return, 0.0)
        self.assertEqual(label.label, 0)

    def test_insufficient_future_returns_none(self):
        closes = [100.0, 101.0]
        self.assertIsNone(
            triple_barrier_label(
                closes, entry_index=1, upper=0.01, lower=0.01, horizon=3
            )
        )


from django.test import TestCase
from apps.stock.models import Stock
from apps.market.models import Candle
from core.market.ingest import MinuteBarBuffer, RealtimeCollector


def _ts(h, m, s):
    return datetime(2024, 1, 2, h, m, s, tzinfo=dt_timezone.utc)


class RealtimeIngestTestCase(TestCase):
    def setUp(self):
        self.stock = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="005930", name="삼성전자"
        )

    def _ticks(self):
        return [
            (self.stock.id, _ts(9, 0, 1), Decimal("100"), Decimal("10")),
            (self.stock.id, _ts(9, 0, 30), Decimal("105"), Decimal("5")),
            (self.stock.id, _ts(9, 0, 59), Decimal("98"), Decimal("7")),
            (self.stock.id, _ts(9, 1, 5), Decimal("101"), Decimal("3")),
            (self.stock.id, _ts(9, 2, 10), Decimal("103"), Decimal("2")),
        ]

    def test_buffer_excludes_current_minute(self):
        buf = MinuteBarBuffer()
        for sid, ts, p, v in self._ticks():
            buf.add(sid, Tick(ts=ts, price=p, volume=v))
        # 현재 시각이 9:02면 9:00, 9:01만 완성 (9:02는 진행 중)
        completed = buf.flush_completed(self.stock.id, _ts(9, 2, 10))
        self.assertEqual([c.opened_at.minute for c in completed], [0, 1])

    def test_consume_persists_all_minutes(self):
        collector = RealtimeCollector(source="test")
        total = collector.consume(self._ticks(), finalize=True)
        self.assertEqual(Candle.objects.filter(stock=self.stock).count(), 3)
        self.assertEqual(total, 3)

        c0 = Candle.objects.get(stock=self.stock, opened_at=_ts(9, 0, 0))
        self.assertEqual(c0.open_price, Decimal("100"))
        self.assertEqual(c0.high_price, Decimal("105"))
        self.assertEqual(c0.low_price, Decimal("98"))
        self.assertEqual(c0.close_price, Decimal("98"))
        self.assertEqual(c0.volume, Decimal("22"))

    def test_idempotent_reingest(self):
        RealtimeCollector(source="test").consume(self._ticks(), finalize=True)
        RealtimeCollector(source="test").consume(self._ticks(), finalize=True)
        # 재수집해도 유니크 키로 중복 없음
        self.assertEqual(Candle.objects.filter(stock=self.stock).count(), 3)


from django.core.management import call_command
from io import StringIO


class CollectRealtimeCommandTestCase(TestCase):
    def test_demo_persists_candles(self):
        Stock.objects.create(market=Stock.Market.KOSPI, symbol="005930", name="삼성전자")
        call_command(
            "collect_realtime", "--symbol", "005930", "--demo",
            "--demo-minutes", "3", stdout=StringIO(),
        )
        self.assertEqual(Candle.objects.filter(source="kis_ws").count(), 3)


from datetime import date
from core.broker.kis.realtime import (
    FIELDS_PER_RECORD,
    KISRealtimeAdapter,
    parse_realtime_message,
)


def _kis_frame(records, tr_id="H0STCNT0", encrypted="0"):
    """H0STCNT0 프레임 합성. records: [(symbol, hhmmss, price, volume), ...]"""
    all_fields = []
    for symbol, hhmmss, price, volume in records:
        rec = [""] * FIELDS_PER_RECORD
        rec[0] = symbol
        rec[1] = hhmmss
        rec[2] = str(price)
        rec[12] = str(volume)
        all_fields += rec
    return "|".join([encrypted, tr_id, str(len(records)), "^".join(all_fields)])


class KISRealtimeParseTestCase(SimpleTestCase):
    def test_parse_single_record(self):
        frame = _kis_frame([("005930", "090001", 70000, 10)])
        ticks = parse_realtime_message(frame, trade_date=date(2024, 1, 2))
        self.assertEqual(len(ticks), 1)
        t = ticks[0]
        self.assertEqual(t.symbol, "005930")
        self.assertEqual(t.price, Decimal("70000"))
        self.assertEqual(t.volume, Decimal("10"))
        self.assertEqual((t.ts.hour, t.ts.minute, t.ts.second), (9, 0, 1))

    def test_parse_multi_record(self):
        frame = _kis_frame([
            ("005930", "090001", 70000, 10),
            ("005930", "090002", 70100, 5),
        ])
        ticks = parse_realtime_message(frame, trade_date=date(2024, 1, 2))
        self.assertEqual(len(ticks), 2)
        self.assertEqual(ticks[1].price, Decimal("70100"))

    def test_non_trade_tr_id_ignored(self):
        frame = _kis_frame([("005930", "090001", 70000, 10)], tr_id="H0STASP0")
        self.assertEqual(parse_realtime_message(frame), [])

    def test_encrypted_raises(self):
        frame = _kis_frame([("005930", "090001", 70000, 10)], encrypted="1")
        with self.assertRaises(ValueError):
            parse_realtime_message(frame)


class KISRealtimeAdapterTestCase(TestCase):
    def setUp(self):
        self.account = None  # 어댑터는 run 경로에서 account를 쓰지 않음
        self.stock = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="005930", name="삼성전자"
        )

    def test_run_persists_candles(self):
        from apps.account.models import Account
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user("kisu", password="pw")
        account = Account.objects.create(
            user=user, broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER, account_number="9",
            name="A", app_key_encrypted="k", app_secret_encrypted="s",
        )
        adapter = KISRealtimeAdapter(account, trade_date=date(2024, 1, 2))
        messages = [
            _kis_frame([("005930", "090001", 70000, 10), ("005930", "090030", 70100, 5)]),
            _kis_frame([("005930", "090105", 70200, 3)]),  # 다음 분 -> 9:00 확정
        ]
        total = adapter.run(messages, finalize=True)
        self.assertEqual(Candle.objects.filter(stock=self.stock, source="kis_ws").count(), 2)
        self.assertEqual(total, 2)
        c0 = Candle.objects.get(stock=self.stock, opened_at__minute=0, source="kis_ws")
        self.assertEqual(c0.open_price, Decimal("70000"))
        self.assertEqual(c0.close_price, Decimal("70100"))
        self.assertEqual(c0.volume, Decimal("15"))
