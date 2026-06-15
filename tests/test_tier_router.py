"""Tests for tiered crawl envelope and router."""

from __future__ import annotations

import unittest

from crawl_envelope import (
    CrawlEnvelope,
    TIER_NAMES,
    attach_envelope,
    normalize_block_reason,
)
from profiles import get_profile, retailers_dict


class TestCrawlEnvelope(unittest.TestCase):
    def test_from_request_uses_profile_defaults(self) -> None:
        profile = get_profile("amazon")
        assert profile is not None
        env = CrawlEnvelope.from_request({}, profile)
        self.assertEqual(env.tier, 3)
        self.assertTrue(env.warm_session)
        self.assertTrue(env.session_id.startswith("amazon-"))

    def test_from_request_honors_override(self) -> None:
        env = CrawlEnvelope.from_request(
            {"tier": 4, "max_tier": 4, "retailer_key": "walmart", "session_id": "custom"},
            get_profile("walmart"),
        )
        self.assertEqual(env.tier, 4)
        self.assertEqual(env.session_id, "custom")

    def test_attach_envelope_blocked(self) -> None:
        out = attach_envelope(
            {"status": "blocked", "url": "https://example.com"},
            tier_used=3,
            session_id="amazon-abc",
            block_reason="captcha",
        )
        self.assertEqual(out["tier_used"], 3)
        self.assertEqual(out["tier_name"], TIER_NAMES[3])
        self.assertIn("captcha", out["detection_hits"])

    def test_normalize_block_reason(self) -> None:
        self.assertEqual(normalize_block_reason("cloudflare_challenge"), "turnstile_challenge")


class TestProfiles(unittest.TestCase):
    def test_retailers_dict_has_five_stores(self) -> None:
        retailers = retailers_dict()
        self.assertEqual(set(retailers.keys()), {"amazon", "walmart", "ebay", "bestbuy", "target"})

    def test_amazon_tier_three(self) -> None:
        profile = get_profile("amazon")
        assert profile is not None
        self.assertEqual(profile["default_tier"], 3)


if __name__ == "__main__":
    unittest.main()
