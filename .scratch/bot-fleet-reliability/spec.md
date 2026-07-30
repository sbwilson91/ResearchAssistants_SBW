# Spec — Bot fleet reliability and maintainability

**Status:** ready-for-agent

## Problem Statement

I run a fleet of scheduled bots (zenodo, citation, journal digest, preprint digest, running bot and its
deep-dive/monthly satellites) that email me and publish a GitHub Pages dashboard. They mostly work, but
the fleet has no safety net and I only find out something is wrong by noticing an email didn't arrive.

Concretely, from my side:

- When a bot breaks, nothing tells me. The dashboard keeps showing the last successful run date, so a
  dead bot looks like a bot that simply hasn't run yet. `status.json` records `"status": "success"` for
  every bot and has never recorded a failure, because each workflow only writes status on `if: success()`.
  The dashboard doesn't even read it — the helper that would read it is dead code.
- A single flaky HTTP call can kill an entire weekly run. Most outbound calls have no retry, so one
  transient 502 from an upstream API loses the whole week's digest, and I won't know (see above).
- Bots overwrite each other's commits. Most workflows commit and push to `main` with no rebase, and
  several of them write the same `status.json`. Overlapping runs mean a lost commit or a hard-failed push.
- Fixing a bug means fixing it three or four times. The Gmail sender exists in four copies (three
  byte-identical), the Gemini summariser in three near-identical copies, and the markdown→HTML email
  converter in two. They have already drifted: one sender swallows exceptions and warns on missing
  credentials, another raises `KeyError` on a missing env var and lets SMTP errors propagate.
- I can't change anything with confidence. There is not a single Python test in ~7,700 lines. The only
  way to find out whether an edit works is to wait for the cron and see if the email arrives.
- Every bot pays for every other bot's dependencies. One flat `requirements.txt` means the citation bot
  installs `sentence-transformers` and `torch` to make a few HTTP calls, and loose `>=` pins mean an
  upstream release can break a scheduled run overnight with no warning and no lockfile to roll back to.

## Solution

Give the fleet a shared spine and a safety net, without changing what any bot produces.

1. **One shared `common/` package.** A single Gmail sender, a single Gemini summariser, and a single
   markdown→HTML email converter, each with one agreed behaviour. Every bot imports from there. Fix a
   bug once.
2. **A real test suite that runs in CI.** Every bot can be run end-to-end in-process against stubbed
   network and a captured mailbox, and asserted on. A pull request that breaks a bot fails before the
   cron does.
3. **Retries and honest failure reporting.** Outbound calls retry transient failures with backoff. Every
   run records its outcome — success *or* failure, with the error — and the dashboard shows a bot that
   failed as visibly broken rather than merely stale. A failed run emails me.
4. **Safe concurrent publishing.** Every workflow that writes to the repo commits and pushes through one
   shared, rebase-and-retry path, and no two runs of the same workflow overlap.
5. **Per-bot pinned dependencies.** Each bot declares only what it needs, pinned to exact versions, so
   CI installs are fast and reproducible and an upstream release can't silently break a scheduled run.

Nothing about the emails, digests, reports, or dashboard content changes. This is entirely about the
fleet being trustworthy and cheap to change.

## User Stories

1. As the fleet owner, I want a bot failure to reach me by email, so that I learn about a break within a
   day instead of the next time I notice a missing digest.
2. As the fleet owner, I want the dashboard to distinguish "failed" from "not due yet", so that a glance
   at the site tells me the true state of the fleet.
3. As the fleet owner, I want each bot's last run outcome recorded including the error message, so that I
   can start diagnosing without opening GitHub Actions logs.
4. As the fleet owner, I want a transient upstream error to be retried, so that one flaky response
   doesn't cost me a whole week's digest.
5. As the fleet owner, I want retries to be bounded and backed off, so that a genuinely-down upstream
   fails fast rather than burning Actions minutes.
6. As the fleet owner, I want a bot that partially fails to still deliver what it did gather, so that one
   dead feed doesn't suppress the other nine.
7. As a developer changing the Gmail sender, I want to change it in exactly one place, so that the fix
   reaches every bot at once.
8. As a developer, I want one agreed behaviour for missing email credentials, so that bots don't disagree
   about whether that's a warning or a crash.
9. As a developer changing the summariser prompt for one bot, I want per-bot prompts to stay per-bot, so
   that consolidating the transport doesn't flatten deliberate differences.
10. As a developer, I want the shared modules to have an explicit public interface, so that I know what I
    can change freely and what bots depend on.
11. As a developer, I want to run the whole test suite locally in seconds with no network and no API
    keys, so that I actually run it.
12. As a developer, I want each bot exercised end-to-end in a test, so that a broken import or a renamed
    field is caught before the cron.
13. As a developer, I want tests to assert on what a bot produces — the email that would be sent, the
    report file written — not on which functions it called, so that tests survive refactoring.
14. As a developer opening a pull request, I want tests to run automatically, so that I can't merge a
    break.
15. As a developer, I want a test that fails when a bot makes a real network call, so that the suite
    can't silently start depending on the internet.
16. As the fleet owner, I want two bots finishing at the same time to both land their commits, so that I
    don't silently lose a digest.
17. As the fleet owner, I want a workflow that can't push to fail loudly rather than dropping the commit,
    so that a lost report is never silent.
18. As the fleet owner, I want two runs of the same bot never to overlap, so that a slow run and its
    successor don't fight over the same output files.
19. As a developer adding a new bot, I want one documented way to commit-and-push from a workflow, so
    that I don't reintroduce the race.
20. As the fleet owner, I want the citation bot's CI job not to install `torch`, so that a five-minute
    job doesn't take fifteen.
21. As the fleet owner, I want dependencies pinned to exact versions, so that a scheduled run behaves the
    same this week as last week.
22. As a developer, I want to know which bot needs which dependency, so that I can upgrade one bot
    without auditing all of them.
23. As a developer, I want dependency upgrades to be a deliberate, reviewable change, so that breakage
    arrives in a pull request rather than at 07:00 on a Monday.
24. As the fleet owner, I want none of this to change the content of my digests, reports, or dashboard,
    so that I can adopt it without re-reading everything I receive.

## Implementation Decisions

**Shared package.** A new top-level `common/` package becomes the single home for the cross-bot
utilities: the Gmail sender, the Gemini summarisation client, and the markdown→HTML email converter. It
is a plain package imported by path, consistent with how bots are already run (`PYTHONPATH` set per
workflow); no packaging or installation step is introduced.

**Reconciling the drifted copies.** The senders disagree today. The agreed behaviour is the tolerant one:
missing credentials logs a warning and returns without sending; SMTP failures are caught, logged, and
reported to the caller rather than raising. Callers that need to know get a return value indicating
whether the send happened. The summariser is split: the *transport* (endpoint, payload shape, retry,
error handling) is shared; the *system prompt and token budget* stay with each bot, passed in by the
caller, because those differences are deliberate.

**Retry policy.** A single shared HTTP helper wraps outbound calls with bounded exponential backoff and
retries only transient conditions (connection errors, timeouts, 429, 5xx). Non-transient responses fail
immediately. Every bot's outbound calls go through it, replacing the ad-hoc retry loops that exist in two
of the summariser copies. Retry counts and delays are parameters with sane defaults, and delays are
injectable so tests don't sleep.

**Run outcome recording.** Recording a run's outcome moves out of the workflow YAML (where it currently
lives as an inline heredoc, guarded by `if: success()`) and into shared Python invoked by every bot. Each
run records bot name, timestamp, outcome, and on failure a short error summary. The existing
`status.json` remains the store and keeps its current shape, extended with the failure fields, so the
dashboard's existing consumer contract is preserved. The dashboard's currently-dead status reader is
wired up so a failed bot renders as failed. A failed run also sends a notification email through the
shared sender.

**Partial failure.** A bot that fails to gather one source but succeeds on others completes, publishes
what it has, and records a degraded outcome rather than a hard failure.

**Publishing safely.** Commit-and-push is unified into one reusable path shared by every workflow that
writes to the repo, performing rebase-onto-remote and retrying a rejected push a bounded number of times
before failing the job. Every writing workflow also gets a concurrency group keyed to itself, so two runs
of the same bot never overlap. Pushes that ultimately fail fail the job.

**Dependencies.** The flat `requirements.txt` is replaced by per-bot requirement sets plus a shared base
for what `common/` needs. Versions are pinned exactly. Each workflow installs only its own bot's set. The
heavy ML stack (sentence-transformers, torch, scikit-learn, pandas) is confined to the journal digest,
which is the only consumer.

**Sequencing.** The shared package lands first and is adopted bot-by-bot rather than in one sweep; the
old per-bot copies are deleted only once no bot imports them. The test harness lands before the
behavioural changes so that retries, status recording, and dependency splits are each verified by tests
rather than by the next cron.

## Testing Decisions

**What makes a good test here.** A good test drives a bot the way the workflow does — call its entry
point — and asserts on what the outside world would observe: the email body that would have been sent,
the report file written to disk, the status record left behind. It must not assert that a particular
helper was called, or reach into module internals, because the whole point is to make the internals
safe to change. No test may touch the network, require an API key, or sleep on a real backoff delay.

**Seams.** Two, and only two:

1. **The bot-run seam.** Each bot's entry point, invoked in-process, with the network stubbed at the
   shared HTTP helper and the shared email sender captured into an in-memory mailbox. Because every bot
   will route outbound HTTP and outbound mail through `common/`, one pair of substitutions covers the
   whole fleet — which is precisely why the shared package is the first ticket. This seam covers the
   shared utilities, retry behaviour, partial-failure handling, and status recording. It is the highest
   available seam and the one to prefer for everything that can be tested there.
2. **The workflow-configuration seam.** Static assertions over the workflow definitions — every workflow
   that writes to the repo uses the shared commit-and-push path, declares a concurrency group, and
   installs only its own bot's dependency set. This exists because the CI race and the dependency split
   are properties of the YAML, unreachable from the bot-run seam. It is deliberately narrow: assertions
   about the fleet's configuration, not about YAML formatting.

**Modules tested.** Through seam 1: the shared email sender, the shared summarisation client, the shared
HTTP/retry helper, the status recorder, and each bot end-to-end (zenodo, citation, preprint digest,
journal digest, running bot). Through seam 2: every workflow that commits.

**Prior art.** `bulbasaur-app/` already follows this shape and is the model: pure logic lives in modules
free of framework globals so `npm test` can drive them with the standard library test runner and no
install step. The Python suite mirrors that — standard-library `unittest` discovery, no new test
framework dependency, runnable as one command with no network and no keys. The existing `test/` layout
in `bulbasaur-app/` is the reference for how tests sit alongside the code they cover.

## Out of Scope

- Any change to what the bots produce — digest content, email styling, report layout, dashboard design,
  summarisation prompts, or schedules.
- `bulbasaur-app/`, which has its own test suite and lifecycle.
- Migrating off Gmail SMTP, off Gemini, or off GitHub Actions.
- Adding new bots, new data sources, or new dashboard features.
- A monitoring or alerting service beyond the failure email — no dashboards, no paging, no third-party
  uptime tooling.
- Type annotations, linting, formatting, or a style pass across the existing code.
- Restructuring the running bot's larger modules (`report.py`, `insights.py`); they are only touched
  where they consume shared utilities.
- Secrets management changes.

## Further Notes

- The five improvements are related but separable. If the programme is cut short, the shared package and
  the test harness are the two that unlock the rest; the dependency split is the most independent and can
  be dropped without affecting the others.
- Three workflows already rebase before pushing (`weekly_digest`, `preprint_run`, `monthly_summary`).
  They are the existing good pattern and should inform the shared path rather than be rewritten around it.
- `read_status_file` in the dashboard is currently dead code — it was written for exactly this purpose and
  never wired up. Treat it as an existing intent to honour, not a new feature.
- `.scratch/` is not currently git-ignored, so these planning artifacts will be committed alongside the
  work unless that changes.
