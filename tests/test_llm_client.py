from __future__ import annotations

import unittest
from unittest.mock import patch

from llm_client import OpenAICompatibleClient, USER_AGENT


class _FakeResponse:
    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"choices":[{"message":{"content":"{\\"sentence\\":\\"Hoi.\\",\\"words_used\\":[]}"}}]}'


class LLMClientTests(unittest.TestCase):
    def test_request_identifies_allai_client(self) -> None:
        client = OpenAICompatibleClient(
            base_url="https://api.groq.com/openai/v1",
            api_key="test-key",
            model="test-model",
        )

        with patch("llm_client.urlopen", return_value=_FakeResponse()) as mocked_urlopen:
            client.generate_sentence("test prompt")

        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), USER_AGENT)
        self.assertEqual(request.get_header("Accept"), "application/json")


if __name__ == "__main__":
    unittest.main()
