"""LLM provider cascade unit tests (NVIDIA → OpenRouter → GitHub → Gemini)."""

import os
import unittest
from unittest.mock import MagicMock

from openai import APIStatusError, RateLimitError

from llm import (
    _ordered_providers,
    _reset_llm_clients,
    _should_use_nvidia_fallback,
)


class TestLlmFallback(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = dict(os.environ)
        _reset_llm_clients()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._orig)
        _reset_llm_clients()

    def test_rate_limit_triggers_fallback(self) -> None:
        err = RateLimitError("rate limited", response=MagicMock(), body=None)
        self.assertTrue(_should_use_nvidia_fallback(err))

    def test_payment_required_triggers_fallback(self) -> None:
        err = APIStatusError(
            "payment required", response=MagicMock(status_code=402), body=None
        )
        self.assertTrue(_should_use_nvidia_fallback(err))

    def test_generic_error_does_not_trigger(self) -> None:
        self.assertFalse(_should_use_nvidia_fallback(ValueError("bad json")))

    def test_ordered_providers_four_way(self) -> None:
        os.environ["NVIDIA_API_KEY"] = "nv-key"
        os.environ["LLM_API_KEY"] = "or-key"
        os.environ["GITHUB_MODELS_TOKEN"] = "gh-key"
        os.environ["GEMINI_API_KEY"] = "gem-key"
        names = [p.name for p in _ordered_providers()]
        self.assertEqual(names, ["nvidia", "openrouter", "github", "gemini"])

    def test_ordered_providers_skips_missing(self) -> None:
        for k in (
            "NVIDIA_API_KEY",
            "LLM_FALLBACK_API_KEY",
            "LLM_API_KEY",
            "OPENROUTER_KEY",
            "OPENROUTER_API_KEY",
            "GITHUB_MODELS_TOKEN",
            "GITHUB_TOKEN",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
        ):
            os.environ.pop(k, None)
        os.environ["GITHUB_TOKEN"] = "ghp_only"
        names = [p.name for p in _ordered_providers()]
        self.assertEqual(names, ["github"])


if __name__ == "__main__":
    unittest.main()
