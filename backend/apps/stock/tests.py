from datetime import datetime, timezone as dt_timezone

from django.test import TestCase

from apps.stock.models import Stock, UniverseMembership, get_universe_stock_ids


class UniverseMembershipTestCase(TestCase):
    """point-in-time 유니버스 조회가 생존편향 없이 시점을 재현하는지 검증."""

    def setUp(self):
        self.a = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="000001", name="A"
        )
        self.b = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="000002", name="B"
        )
        self.c = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="000003", name="C"
        )

        U = UniverseMembership.Universe.KOSPI200
        # A: 2023-01-01 편입, 2023-06-01 편출
        UniverseMembership.objects.create(
            universe=U,
            stock=self.a,
            effective_from=datetime(2023, 1, 1, tzinfo=dt_timezone.utc),
            effective_to=datetime(2023, 6, 1, tzinfo=dt_timezone.utc),
        )
        # B: 2023-03-01 편입, 현재까지
        UniverseMembership.objects.create(
            universe=U,
            stock=self.b,
            effective_from=datetime(2023, 3, 1, tzinfo=dt_timezone.utc),
            effective_to=None,
        )
        # C: 다른 유니버스(CUSTOM) -> KOSPI200 조회에 잡히면 안 됨
        UniverseMembership.objects.create(
            universe=UniverseMembership.Universe.CUSTOM,
            stock=self.c,
            effective_from=datetime(2023, 1, 1, tzinfo=dt_timezone.utc),
        )

    def test_membership_before_b_joins(self):
        at = datetime(2023, 2, 1, tzinfo=dt_timezone.utc)  # A만 편입 상태
        ids = get_universe_stock_ids("KOSPI200", at=at)
        self.assertEqual(set(ids), {self.a.id})

    def test_membership_both_active(self):
        at = datetime(2023, 4, 1, tzinfo=dt_timezone.utc)  # A, B 둘 다
        ids = get_universe_stock_ids("KOSPI200", at=at)
        self.assertEqual(set(ids), {self.a.id, self.b.id})

    def test_membership_after_a_leaves(self):
        at = datetime(2023, 7, 1, tzinfo=dt_timezone.utc)  # A 편출, B만
        ids = get_universe_stock_ids("KOSPI200", at=at)
        self.assertEqual(set(ids), {self.b.id})

    def test_effective_to_is_exclusive(self):
        # A 편출 시각 정각(2023-06-01)에는 이미 유니버스에서 빠진다 (구간 끝 미포함)
        at = datetime(2023, 6, 1, tzinfo=dt_timezone.utc)
        ids = get_universe_stock_ids("KOSPI200", at=at)
        self.assertEqual(set(ids), {self.b.id})

    def test_other_universe_excluded(self):
        at = datetime(2023, 4, 1, tzinfo=dt_timezone.utc)
        ids = get_universe_stock_ids("KOSPI200", at=at)
        self.assertNotIn(self.c.id, ids)


from django.test import SimpleTestCase
from core.universe.filter import (
    CandidateConfig,
    StockSnapshot,
    is_entry_window,
    passes_filters,
    select_candidates,
)


def _snap(sid, score=0.0, turnover=2e8, recent_volume=1000.0, vol=0.002,
          spread=10.0, halted=False):
    return StockSnapshot(
        stock_id=sid, turnover=turnover, recent_volume=recent_volume,
        volatility=vol, score=score, spread_bps=spread, halted=halted,
    )


class CandidateFilterTestCase(SimpleTestCase):
    def setUp(self):
        self.cfg = CandidateConfig()

    def test_default_passes(self):
        self.assertTrue(passes_filters(_snap(1), self.cfg))

    def test_halted_excluded(self):
        self.assertFalse(passes_filters(_snap(1, halted=True), self.cfg))

    def test_low_turnover_excluded(self):
        self.assertFalse(passes_filters(_snap(1, turnover=1e7), self.cfg))

    def test_wide_spread_excluded(self):
        self.assertFalse(passes_filters(_snap(1, spread=100.0), self.cfg))

    def test_out_of_band_volatility_excluded(self):
        self.assertFalse(passes_filters(_snap(1, vol=0.05), self.cfg))   # 너무 높음
        self.assertFalse(passes_filters(_snap(1, vol=0.0001), self.cfg))  # 너무 낮음

    def test_select_ranks_and_limits_top_k(self):
        snaps = [_snap(10, score=1.0), _snap(20, score=3.0), _snap(30, score=2.0)]
        cfg = CandidateConfig(top_k=2)
        self.assertEqual(select_candidates(snaps, cfg), [20, 30])  # score 내림차순 top2

    def test_entry_window_gating(self):
        snaps = [_snap(10, score=1.0)]
        self.assertEqual(select_candidates(snaps, self.cfg, minutes_since_open=2), [])    # 개장 초
        self.assertEqual(select_candidates(snaps, self.cfg, minutes_since_open=380), [])  # 종가 근접
        self.assertEqual(select_candidates(snaps, self.cfg, minutes_since_open=100), [10])

    def test_is_entry_window(self):
        self.assertTrue(is_entry_window(None, self.cfg))
        self.assertFalse(is_entry_window(0, self.cfg))
        self.assertTrue(is_entry_window(100, self.cfg))


from datetime import datetime as _dt, timezone as _tz
from decimal import Decimal as _D
from io import StringIO as _SIO
from django.core.management import call_command as _cc
from apps.market.models import Candle as _Candle
from apps.stock.models import (
    UniverseMembership as _UM,
    get_collection_stock_ids as _get_collection,
    sync_collection_universe as _sync_collection,
)


class CollectionUniverseTestCase(TestCase):
    def setUp(self):
        self.a = Stock.objects.create(market=Stock.Market.KOSPI, symbol="000001", name="A")
        self.b = Stock.objects.create(market=Stock.Market.KOSPI, symbol="000002", name="B")

    def test_sync_is_idempotent_and_appends(self):
        added = _sync_collection([self.a, self.b])
        self.assertEqual(added, 2)
        self.assertEqual(set(_get_collection()), {self.a.id, self.b.id})

        # 재실행: 이미 활성이면 추가 없음
        again = _sync_collection([self.a, self.b])
        self.assertEqual(again, 0)
        # 활성 편입 레코드는 종목당 1개
        self.assertEqual(
            _UM.objects.filter(universe=_UM.Universe.COLLECTION, effective_to__isnull=True).count(),
            2,
        )

    def test_command_all_with_candles(self):
        _Candle.objects.create(
            stock=self.a, timeframe=Candle_TF_DAY, opened_at=_dt(2024, 1, 2, tzinfo=_tz.utc),
            open_price=_D("100"), high_price=_D("100"), low_price=_D("100"),
            close_price=_D("100"), volume=_D("1"), source="test",
        )
        _cc("sync_collection_universe", "--all-with-candles", stdout=_SIO())
        self.assertIn(self.a.id, _get_collection())
        self.assertNotIn(self.b.id, _get_collection())  # 캔들 없는 종목은 제외


Candle_TF_DAY = _Candle.Timeframe.DAY_1


class RebalanceUniverseTestCase(TestCase):
    def test_rebalance_adds_and_removes(self):
        from apps.stock.models import (
            UniverseMembership as UM,
            get_universe_stock_ids,
            rebalance_universe,
        )
        a = Stock.objects.create(market=Stock.Market.KOSPI, symbol="A1", name="A")
        b = Stock.objects.create(market=Stock.Market.KOSPI, symbol="B1", name="B")
        c = Stock.objects.create(market=Stock.Market.KOSPI, symbol="C1", name="C")

        r1 = rebalance_universe(UM.Universe.KOSPI200, [a, b])
        self.assertEqual(r1["added"], 2)
        self.assertEqual(set(get_universe_stock_ids(UM.Universe.KOSPI200)), {a.id, b.id})

        # 리밸런싱: A 편출, C 편입, B 유지
        r2 = rebalance_universe(UM.Universe.KOSPI200, [b, c])
        self.assertEqual(r2["added"], 1)
        self.assertEqual(r2["removed"], 1)
        self.assertEqual(set(get_universe_stock_ids(UM.Universe.KOSPI200)), {b.id, c.id})
