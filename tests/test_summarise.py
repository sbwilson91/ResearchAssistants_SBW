"""The shared summarisation transport, and the per-bot voices layered on it."""
import unittest
import unittest.mock

import requests

from common import summarise as summarise_module
from common.summarise import summarise
from tests.support import (
    FakeResponse,
    FakeSession,
    RecordingSleep,
    load_bot_module,
    ok,
)

API_KEY = {"api_key": "test-key"}


class Payload(unittest.TestCase):
    def test_sends_the_callers_prompt_and_token_budget(self):
        session = FakeSession([ok("a summary")])

        summarise(
            "some text",
            system_prompt="be terse",
            max_tokens=321,
            session=session,
            **API_KEY,
        )

        body = session.calls[0]["json"]
        self.assertEqual(
            body["system_instruction"]["parts"][0]["text"], "be terse"
        )
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "some text")
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 321)

    def test_targets_the_requested_model(self):
        session = FakeSession([ok("x")])

        summarise(
            "t",
            system_prompt="p",
            max_tokens=10,
            model="gemini-2.5-flash-lite",
            session=session,
            **API_KEY,
        )

        self.assertIn("gemini-2.5-flash-lite", session.calls[0]["url"])

    def test_authenticates_with_the_api_key(self):
        session = FakeSession([ok("x")])

        summarise("t", system_prompt="p", max_tokens=10, session=session, **API_KEY)

        self.assertEqual(session.calls[0]["params"], {"key": "test-key"})

    def test_reads_the_api_key_from_the_environment_when_not_passed(self):
        session = FakeSession([ok("x")])

        with unittest.mock.patch.dict(
            "os.environ", {"GOOGLE_API_KEY": "from-env"}, clear=False
        ):
            summarise("t", system_prompt="p", max_tokens=10, session=session)

        self.assertEqual(session.calls[0]["params"], {"key": "from-env"})

    def test_raises_when_no_api_key_is_available(self):
        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                summarise("t", system_prompt="p", max_tokens=10)


class Unwrapping(unittest.TestCase):
    def test_returns_the_generated_text_stripped(self):
        session = FakeSession([ok("  a summary  ")])

        result = summarise(
            "t", system_prompt="p", max_tokens=10, session=session, **API_KEY
        )

        self.assertEqual(result, "a summary")

    def test_returns_empty_string_when_the_model_returned_no_candidates(self):
        session = FakeSession([FakeResponse(json_body={"candidates": []})])

        result = summarise(
            "t", system_prompt="p", max_tokens=10, session=session, **API_KEY
        )

        self.assertEqual(result, "")

    def test_returns_empty_string_when_a_candidate_has_no_parts(self):
        session = FakeSession(
            [FakeResponse(json_body={"candidates": [{"content": {"parts": []}}]})]
        )

        result = summarise(
            "t", system_prompt="p", max_tokens=10, session=session, **API_KEY
        )

        self.assertEqual(result, "")


class RetryBehaviour(unittest.TestCase):
    """Summarisation inherits the shared retry contract rather than its own."""

    def test_retries_a_rate_limit_then_succeeds(self):
        session = FakeSession([FakeResponse(429), ok("recovered")])

        result = summarise(
            "t",
            system_prompt="p",
            max_tokens=10,
            session=session,
            sleep=RecordingSleep(),
            **API_KEY,
        )

        self.assertEqual(result, "recovered")
        self.assertEqual(session.call_count, 2)

    def test_raises_rather_than_returning_empty_when_retries_are_exhausted(self):
        """The old per-bot loop fell through and returned "" — a silent empty
        summary in the digest. Exhaustion must be loud."""
        session = FakeSession([FakeResponse(503)] * 3)

        with self.assertRaises(requests.exceptions.HTTPError):
            summarise(
                "t",
                system_prompt="p",
                max_tokens=10,
                session=session,
                sleep=RecordingSleep(),
                **API_KEY,
            )

    def test_a_client_error_fails_immediately(self):
        session = FakeSession([FakeResponse(400)])
        sleep = RecordingSleep()

        with self.assertRaises(requests.exceptions.HTTPError):
            summarise(
                "t",
                system_prompt="p",
                max_tokens=10,
                session=session,
                sleep=sleep,
                **API_KEY,
            )

        self.assertEqual(session.call_count, 1)
        self.assertEqual(sleep.delays, [])


class BotVoicesArePreserved(unittest.TestCase):
    """Consolidating the transport must not flatten deliberate differences."""

    def _patched_session(self, get_ai_summary, text):
        session = FakeSession([ok("summary")])
        real_post = summarise_module.http.post

        def post_with_session(url, **kwargs):
            return real_post(url, session=session, sleep=RecordingSleep(), **kwargs)

        with unittest.mock.patch.object(
            summarise_module.http, "post", post_with_session
        ):
            with unittest.mock.patch.dict(
                "os.environ", {"GOOGLE_API_KEY": "k"}, clear=False
            ):
                get_ai_summary(text)
        return session.calls[0]["json"]

    def test_zenodo_keeps_its_prompt_budget_and_html_stripping(self):
        get_ai_summary = load_bot_module(
            "zenodo_bot/utils/ai_logic.py"
        ).get_ai_summary

        body = self._patched_session(
            get_ai_summary, "<p>A <b>dataset</b> description</p>"
        )

        self.assertIn(
            "scientific data analyst", body["system_instruction"]["parts"][0]["text"]
        )
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 500)
        self.assertEqual(
            body["contents"][0]["parts"][0]["text"], "A dataset description"
        )

    def test_zenodo_truncates_long_descriptions(self):
        get_ai_summary = load_bot_module(
            "zenodo_bot/utils/ai_logic.py"
        ).get_ai_summary

        body = self._patched_session(get_ai_summary, "x" * 5000)

        self.assertEqual(len(body["contents"][0]["parts"][0]["text"]), 1500)

    def test_citation_keeps_its_own_prompt_and_larger_budget(self):
        get_ai_summary = load_bot_module(
            "citation_bot/utils/ai_logic.py"
        ).get_ai_summary

        body = self._patched_session(get_ai_summary, "An abstract.")

        self.assertIn(
            "scientific literature analyst",
            body["system_instruction"]["parts"][0]["text"],
        )
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 1024)
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "An abstract.")

    def test_the_two_bots_send_different_system_prompts(self):
        citation = load_bot_module(
            "citation_bot/utils/ai_logic.py"
        ).get_ai_summary
        zenodo = load_bot_module("zenodo_bot/utils/ai_logic.py").get_ai_summary

        zenodo_prompt = self._patched_session(zenodo, "t")[
            "system_instruction"
        ]["parts"][0]["text"]
        citation_prompt = self._patched_session(citation, "t")[
            "system_instruction"
        ]["parts"][0]["text"]

        self.assertNotEqual(zenodo_prompt, citation_prompt)


if __name__ == "__main__":
    unittest.main()
