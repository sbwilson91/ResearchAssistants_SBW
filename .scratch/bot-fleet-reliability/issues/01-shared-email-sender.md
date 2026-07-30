# 01 — Shared email sender in `common/`, adopted by the zenodo bot

**What to build:** A single Gmail sender lives in a new top-level `common/` package with one agreed
behaviour, and the zenodo bot sends its weekly email through it instead of its own copy. From the fleet
owner's perspective nothing changes — the same email arrives, from the same address, with the same
content. What changes is that there is now one sender to fix instead of four.

The agreed behaviour, reconciling the four drifted copies: missing credentials logs a warning and
returns without sending rather than raising; SMTP failures are caught and logged rather than propagating;
the caller can tell from the return value whether the send actually happened.

This is the prefactor that every other ticket rests on — it establishes `common/` as a place bots import
from, and gives the test harness a single point at which outbound mail can be captured.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] `common/` exists as a top-level package importable by the bots under their existing `PYTHONPATH`
      arrangement, with no packaging or install step introduced
- [ ] It contains one email sender exposing an explicit public interface
- [ ] Missing credentials produce a warning and a "not sent" result, never an exception
- [ ] An SMTP failure produces a logged error and a "not sent" result, never an exception
- [ ] A successful send produces a "sent" result
- [ ] The zenodo bot imports the shared sender and its own copy is deleted
- [ ] The zenodo bot's workflow still resolves the import when run the way the workflow runs it
- [ ] The other bots' copies are untouched — they migrate in ticket 05
