import unittest

from shortly.llm.base import (
    LLMError,
    LLMQuotaExceeded,
    normalize_provider_error,
)


class _ProviderError(RuntimeError):
    def __init__(self, message: str, *, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class CommercialAIErrorTests(unittest.TestCase):
    def test_openai_insufficient_quota_is_direct_user_error(self) -> None:
        error = normalize_provider_error(
            "openai",
            _ProviderError("billing check failed", code="insufficient_quota"),
        )

        self.assertIsInstance(error, LLMQuotaExceeded)
        self.assertEqual(error.error_code, "commercial_ai_quota_exceeded")
        self.assertIn("OpenAI", str(error))
        self.assertIn("토큰·크레딧", str(error))
        self.assertIn("잔액과 사용 한도", str(error))

    def test_anthropic_low_credit_message_is_direct_user_error(self) -> None:
        error = normalize_provider_error(
            "anthropic",
            _ProviderError(
                "Your credit balance is too low to access the Anthropic API. "
                "Please purchase credits."
            ),
        )

        self.assertIsInstance(error, LLMQuotaExceeded)
        self.assertIn("Anthropic", str(error))

    def test_gemini_resource_exhausted_is_direct_user_error(self) -> None:
        error = normalize_provider_error(
            "gemini",
            _ProviderError("RESOURCE_EXHAUSTED: quota exceeded"),
        )

        self.assertIsInstance(error, LLMQuotaExceeded)
        self.assertIn("Gemini", str(error))

    def test_temporary_rate_limit_is_not_reported_as_balance_shortage(self) -> None:
        error = normalize_provider_error(
            "openai",
            _ProviderError("Too many requests", code="rate_limit_exceeded"),
        )

        self.assertIsInstance(error, LLMError)
        self.assertNotIsInstance(error, LLMQuotaExceeded)

    def test_output_token_limit_is_not_reported_as_balance_shortage(self) -> None:
        error = normalize_provider_error(
            "anthropic",
            _ProviderError("maximum output tokens exceeded"),
        )

        self.assertIsInstance(error, LLMError)
        self.assertNotIsInstance(error, LLMQuotaExceeded)


if __name__ == "__main__":
    unittest.main()
