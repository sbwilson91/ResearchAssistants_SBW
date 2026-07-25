# 04 — Retrying HTTP helper and shared summarisation transport

**What to build:** A transient upstream failure no longer costs a whole week's digest. Outbound calls
retry with bounded exponential backoff on connection errors, timeouts, 429s and 5xx responses, and fail
immediately on anything else. The Gemini summarisation transport becomes shared — one endpoint, payload
shape, retry policy and error handling — while each bot keeps its own system prompt and token budget,
because those differences are deliberate.

The zenodo and citation bots adopt both, replacing the two ad-hoc retry loops that exist today.

**Blocked by:** 02 — Test harness and the first end-to-end bot test.

**Status:** ready-for-agent

- [ ] A shared HTTP helper in `common/` retries transient failures with bounded exponential backoff
- [ ] Connection errors, timeouts, 429 and 5xx are retried; other non-success responses fail immediately
- [ ] Retry count and backoff delays are parameters with sane defaults, and delays are injectable so
      tests never sleep on a real backoff
- [ ] A shared summarisation client in `common/` owns the endpoint, payload shape, retry and error
      handling, and takes the system prompt and token budget from the caller
- [ ] The zenodo and citation bots' summaries are unchanged in content — each still uses its own prompt
      and token budget
- [ ] The zenodo and citation bots' ad-hoc retry loops are deleted
- [ ] Tests cover: retry-then-succeed, exhausted retries, and immediate failure on a non-transient
      response
