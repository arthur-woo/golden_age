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
