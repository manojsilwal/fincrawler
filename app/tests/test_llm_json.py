"""LLM JSON parsing unit tests."""

import unittest

from llm import _extract_json_objects, _message_candidates_for_json, _parse_json_response


class _FakeMessage:
    def __init__(self, content="", reasoning=""):
        self.content = content
        self.reasoning = reasoning


class TestLlmJson(unittest.TestCase):
    def test_parse_json_from_reasoning_prose(self) -> None:
        raw = (
            "The user wants me to extract stock quote data from a Yahoo Finance page for MSFT.\n"
            "Looking at the screenshots I see the price is 412.50.\n"
            '{"quote_header": {"ticker": "MSFT", "regularMarketPrice": 412.5}}'
        )
        parsed = _parse_json_response(raw)
        self.assertNotIn("_error", parsed)
        self.assertEqual(parsed["quote_header"]["ticker"], "MSFT")
        self.assertEqual(parsed["quote_header"]["regularMarketPrice"], 412.5)

    def test_parse_json_from_markdown_fence(self) -> None:
        raw = 'Here is the data:\n```json\n{"ticker": "AAPL", "regularMarketPrice": 190.2}\n```'
        parsed = _parse_json_response(raw)
        self.assertEqual(parsed["ticker"], "AAPL")

    def test_extract_json_objects_nested(self) -> None:
        raw = 'prefix {"a": {"b": 1}} suffix'
        objs = _extract_json_objects(raw)
        self.assertEqual(len(objs), 1)
        self.assertIn('"b": 1', objs[0])

    def test_salvage_finance_fields_from_prose(self) -> None:
        raw = (
            "I can see MSFT at regularMarketPrice: 412.5 in the header but JSON failed"
        )
        parsed = _parse_json_response(raw)
        self.assertEqual(parsed.get("_error"), "json_parse_failed")

    def test_salvage_partial_quote_header(self) -> None:
        raw = '{"quote_header": {"ticker": "MSFT", "regularMarketPrice": 412.5'
        parsed = _parse_json_response(raw)
        self.assertNotIn("_error", parsed)
        self.assertEqual(parsed["quote_header"]["ticker"], "MSFT")

    def test_message_candidates_prefers_content_and_reasoning(self) -> None:
        msg = _FakeMessage(content="", reasoning='thought {"ticker": "MSFT"}')
        candidates = _message_candidates_for_json(msg)
        self.assertEqual(len(candidates), 1)
        self.assertIn("MSFT", candidates[0])

    def test_message_candidates_includes_both_fields(self) -> None:
        msg = _FakeMessage(content='{"ticker": "MSFT"}', reasoning="thinking...")
        candidates = _message_candidates_for_json(msg)
        self.assertGreaterEqual(len(candidates), 1)
        self.assertIn('{"ticker": "MSFT"}', candidates[0])


if __name__ == "__main__":
    unittest.main()
