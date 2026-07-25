"""Test doubles for driving bot code without touching the network.

Nothing here sleeps, and nothing here opens a socket. A test that wants to
observe backoff reads `RecordingSleep.delays` instead of waiting.
"""
import importlib.util
import pathlib

import requests

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_bot_module(relative_path):
    """Import a bot module by file path, e.g. "zenodo_bot/utils/ai_logic.py".

    The bots run with their own folder on PYTHONPATH, so their `utils` package
    re-exports under a top-level `utils` name that only exists at runtime.
    Loading the file directly gets at the module the way the bot sees it,
    without triggering that package's __init__.
    """
    path = REPO_ROOT / relative_path
    name = relative_path.replace("/", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    """The parts of requests.Response the fleet actually uses."""

    def __init__(self, status_code=200, json_body=None, headers=None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.headers = headers if headers is not None else {
            "Content-Type": "application/json"
        }

    def json(self):
        return self._json_body

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Error", response=self
            )


class FakeSession:
    """A requests-shaped session that replays a scripted list of outcomes.

    Each entry is either a FakeResponse to return or an exception to raise.
    Every call is recorded so tests can assert on what was sent.
    """

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self._outcomes:
            raise AssertionError(
                f"FakeSession ran out of scripted outcomes on call "
                f"{len(self.calls)}: {method} {url}"
            )
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def call_count(self):
        return len(self.calls)


class RecordingSleep:
    """Stands in for time.sleep, recording what it was asked to wait."""

    def __init__(self):
        self.delays = []

    def __call__(self, seconds):
        self.delays.append(seconds)


def ok(text):
    """A well-formed Gemini success response carrying `text`."""
    return FakeResponse(
        json_body={"candidates": [{"content": {"parts": [{"text": text}]}}]}
    )
