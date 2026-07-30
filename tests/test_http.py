"""The retry contract: what gets retried, what fails fast, what backoff runs."""
import unittest

import requests

from common import http
from tests.support import FakeResponse, FakeSession, RecordingSleep


class RetryOnTransientFailures(unittest.TestCase):
    def test_retries_then_succeeds(self):
        session = FakeSession([FakeResponse(503), FakeResponse(429), FakeResponse(200)])
        sleep = RecordingSleep()

        response = http.get("https://example.test/x", session=session, sleep=sleep)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.call_count, 3)

    def test_retries_connection_errors_and_timeouts(self):
        for exc in (
            requests.exceptions.ConnectionError("refused"),
            requests.exceptions.Timeout("slow"),
        ):
            with self.subTest(exception=type(exc).__name__):
                session = FakeSession([exc, FakeResponse(200)])

                response = http.get(
                    "https://example.test/x", session=session, sleep=RecordingSleep()
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(session.call_count, 2)

    def test_backs_off_exponentially_between_attempts(self):
        session = FakeSession([FakeResponse(503), FakeResponse(503), FakeResponse(200)])
        sleep = RecordingSleep()

        http.get("https://example.test/x", session=session, sleep=sleep, backoff=5.0)

        self.assertEqual(sleep.delays, [5.0, 10.0])

    def test_does_not_sleep_when_the_first_attempt_succeeds(self):
        sleep = RecordingSleep()

        http.get(
            "https://example.test/x",
            session=FakeSession([FakeResponse(200)]),
            sleep=sleep,
        )

        self.assertEqual(sleep.delays, [])


class ExhaustedRetries(unittest.TestCase):
    def test_raises_the_last_http_error_rather_than_returning_it(self):
        session = FakeSession([FakeResponse(503), FakeResponse(503), FakeResponse(503)])

        with self.assertRaises(requests.exceptions.HTTPError):
            http.get(
                "https://example.test/x", session=session, sleep=RecordingSleep()
            )

        self.assertEqual(session.call_count, 3)

    def test_raises_the_connection_error_when_every_attempt_fails(self):
        session = FakeSession(
            [requests.exceptions.ConnectionError("refused")] * 3
        )

        with self.assertRaises(requests.exceptions.ConnectionError):
            http.get(
                "https://example.test/x", session=session, sleep=RecordingSleep()
            )

        self.assertEqual(session.call_count, 3)

    def test_sleeps_between_attempts_but_not_after_the_last(self):
        sleep = RecordingSleep()
        session = FakeSession([FakeResponse(503)] * 3)

        with self.assertRaises(requests.exceptions.HTTPError):
            http.get("https://example.test/x", session=session, sleep=sleep)

        self.assertEqual(len(sleep.delays), 2)

    def test_retries_is_configurable(self):
        session = FakeSession([FakeResponse(503)] * 5)

        with self.assertRaises(requests.exceptions.HTTPError):
            http.get(
                "https://example.test/x",
                session=session,
                sleep=RecordingSleep(),
                retries=5,
            )

        self.assertEqual(session.call_count, 5)

    def test_rejects_a_retry_count_below_one(self):
        with self.assertRaises(ValueError):
            http.get("https://example.test/x", retries=0)


class NonTransientFailures(unittest.TestCase):
    def test_fails_immediately_without_retrying(self):
        for status in (400, 401, 403, 404, 422):
            with self.subTest(status=status):
                session = FakeSession([FakeResponse(status)])
                sleep = RecordingSleep()

                with self.assertRaises(requests.exceptions.HTTPError):
                    http.get("https://example.test/x", session=session, sleep=sleep)

                self.assertEqual(session.call_count, 1)
                self.assertEqual(sleep.delays, [])


class RequestPlumbing(unittest.TestCase):
    def test_passes_method_url_and_kwargs_through(self):
        session = FakeSession([FakeResponse(200)])

        http.post(
            "https://example.test/thing",
            session=session,
            params={"key": "abc"},
            json={"a": 1},
            timeout=30,
        )

        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], "https://example.test/thing")
        self.assertEqual(call["params"], {"key": "abc"})
        self.assertEqual(call["json"], {"a": 1})
        self.assertEqual(call["timeout"], 30)

    def test_retry_controls_are_not_forwarded_to_requests(self):
        session = FakeSession([FakeResponse(200)])

        http.get("https://example.test/x", session=session, sleep=RecordingSleep())

        for control in ("retries", "backoff", "sleep", "session"):
            self.assertNotIn(control, session.calls[0])


if __name__ == "__main__":
    unittest.main()
