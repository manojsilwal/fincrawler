"""Ranker tests."""

from datetime import datetime, timedelta, timezone

from app.services.ranking.product_ranker import ProductRanker


def test_in_stock_lower_price_ranks_higher():
    r = ProductRanker()
    offers = [
        {"price": 500, "availability": "in_stock", "last_seen_at": datetime.now(timezone.utc)},
        {"price": 400, "availability": "in_stock", "last_seen_at": datetime.now(timezone.utc)},
    ]
    ranked = r.rank_offers(offers)
    assert ranked[0]["price"] == 400


def test_out_of_stock_ranks_lower():
    r = ProductRanker()
    now = datetime.now(timezone.utc)
    s_in = r.score_offer({"price": 400, "availability": "in_stock", "last_seen_at": now}, [400, 500])
    s_out = r.score_offer({"price": 300, "availability": "out_of_stock", "last_seen_at": now}, [300, 500])
    assert s_in > s_out


def test_fresh_ranks_higher():
    r = ProductRanker()
    now = datetime.now(timezone.utc)
    stale = now - timedelta(days=40)
    s_fresh = r.score_offer({"price": 400, "availability": "in_stock", "last_seen_at": now}, [400])
    s_stale = r.score_offer({"price": 400, "availability": "in_stock", "last_seen_at": stale}, [400])
    assert s_fresh > s_stale


def test_score_between_0_and_1():
    r = ProductRanker()
    s = r.score_offer({"price": 100, "availability": "unknown"}, [100, 200])
    assert 0.0 <= s <= 1.0
