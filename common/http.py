"""Outbound HTTP with bounded retry on transient failures.

Every bot's outbound calls go through here so that one flaky response from an
upstream API doesn't cost a whole week's digest.

Retried:     connection errors, timeouts, 429, and 5xx responses.
Not retried: any other non-2xx response — a 404 or a 401 won't fix itself, so
             it fails immediately rather than burning three backoff delays.

Exhausting the retries re-raises the last failure. It never returns a failed
response, because a caller that forgets to check would otherwise parse an error
body as if it were data.
"""
import time

import requests

# 5xx are transient by assumption: the upstreams here (Zenodo, OpenAlex, Gemini)
# return them for overload rather than for a malformed request.
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})

TRANSIENT_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)

DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 5.0


def request(
    method,
    url,
    *,
    retries=DEFAULT_RETRIES,
    backoff=DEFAULT_BACKOFF,
    sleep=time.sleep,
    session=None,
    **kwargs,
):
    """Make an HTTP request, retrying transient failures with exponential backoff.

    Args:
        method:  HTTP verb, e.g. "GET".
        url:     Full request URL.
        retries: Total attempts, not counting-from-zero retries. Must be >= 1.
        backoff: Seconds before the first retry; doubles each attempt
                 (5s, 10s, 20s by default).
        sleep:   Injected so tests never wait on a real backoff delay.
        session: A requests.Session, or None to use the module-level API.
        **kwargs: Passed through to requests — params, json, timeout, ...

    Returns:
        The successful requests.Response.

    Raises:
        requests.HTTPError on a non-2xx response, immediately for a
        non-transient status or after the final attempt for a transient one.
        requests.RequestException if connecting failed on every attempt.
    """
    if retries < 1:
        raise ValueError(f"retries must be at least 1, got {retries}")

    caller = session if session is not None else requests
    last_attempt = retries - 1

    for attempt in range(retries):
        try:
            response = caller.request(method, url, **kwargs)
        except TRANSIENT_EXCEPTIONS as exc:
            if attempt == last_attempt:
                raise
            _wait(f"{type(exc).__name__}", attempt, backoff, sleep)
            continue

        if response.status_code in TRANSIENT_STATUS and attempt != last_attempt:
            _wait(f"HTTP {response.status_code}", attempt, backoff, sleep)
            continue

        # Either a success, a non-transient failure, or a transient one on the
        # final attempt — raise_for_status sorts out which.
        response.raise_for_status()
        return response

    raise AssertionError("unreachable: the loop returns or raises on every path")


def get(url, **kwargs):
    """GET `url`, retrying transient failures. See `request`."""
    return request("GET", url, **kwargs)


def post(url, **kwargs):
    """POST to `url`, retrying transient failures. See `request`."""
    return request("POST", url, **kwargs)


def _wait(reason, attempt, backoff, sleep):
    delay = backoff * (2 ** attempt)
    print(f"  {reason}, retrying in {delay:.0f}s…")
    sleep(delay)
