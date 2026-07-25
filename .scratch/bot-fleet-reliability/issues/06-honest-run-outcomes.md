# 06 — Honest run outcomes: failures recorded, emailed, and shown on the dashboard

**What to build:** When a bot breaks, the fleet owner finds out. A failed run emails them with the error,
records the failure rather than leaving the last success in place, and the dashboard shows that bot as
broken instead of merely stale. A bot that loses one source but succeeds on the others still publishes
what it gathered and records a degraded outcome.

Today every recorded status is `"success"` — failures are never written, because recording lives in an
inline workflow step guarded on success. Recording moves into shared Python that every bot invokes, so it
runs whatever the outcome. The dashboard's status reader was written for exactly this and never wired
up; this ticket honours that intent.

**Blocked by:** 05 — Remaining bots migrate onto `common/`.

**Status:** ready-for-agent

- [ ] Run outcome recording lives in `common/` and is invoked by every bot, not by workflow YAML
- [ ] A run records bot name, timestamp and outcome — success, degraded, or failure
- [ ] A failed run records a short error summary alongside the outcome
- [ ] A failed run sends a notification email through the shared sender
- [ ] The existing status store keeps its current shape and location, extended with the failure fields,
      so nothing that reads it today breaks
- [ ] A bot that fails one source and succeeds on others completes, publishes what it gathered, and
      records a degraded outcome
- [ ] The dashboard reads the status store and renders a failed bot as visibly failed, distinct from a
      bot that is simply not due yet
- [ ] The inline status-writing steps are removed from the workflows
- [ ] Tests at the bot-run seam cover success, degraded and failure paths, including the notification
      email
