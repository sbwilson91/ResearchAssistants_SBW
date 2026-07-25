# 05 — Remaining bots migrate onto `common/`, duplicates deleted

**What to build:** Every bot in the fleet sends mail, calls out over HTTP, and renders markdown email
through the shared package, and the duplicate copies are gone. Fixing a bug now means fixing it once.

The preprint digest, journal digest and running bot migrate here; the markdown→HTML email converter joins
`common/` as part of this ticket, reconciling its two copies the same way the sender was reconciled in
ticket 01.

This is the contract half of an expand–contract migration: the shared forms already exist and are
proven, so each bot moves over and its private copy is deleted. Migrate bot by bot, keeping the suite
green between bots, rather than in one sweep.

**Blocked by:** 04 — Retrying HTTP helper and shared summarisation transport.

**Status:** ready-for-agent

- [ ] The markdown→HTML email converter lives in `common/` with one agreed behaviour
- [ ] The preprint digest, journal digest and running bot use the shared sender, HTTP helper and
      converter
- [ ] Every per-bot copy of the sender, summariser and converter is deleted; no bot retains a private one
- [ ] Each migrated bot has an end-to-end test at the bot-run seam asserting on its produced email and
      output files
- [ ] Digest, report and email content is unchanged for every migrated bot
- [ ] Deliberate per-bot differences — prompts, token budgets, subject lines — are preserved, not
      flattened
- [ ] Each bot's workflow still resolves its imports when run the way the workflow runs it
