# 07 — Safe concurrent publishing: one commit-and-push path, no overlapping runs

**What to build:** Two bots finishing at the same time both land their commits, and a push that can't
land fails the job loudly instead of dropping the report silently. Two runs of the same bot never
overlap.

Today most workflows commit and push to the main branch with no rebase, and several write the same
status file — so overlapping runs mean a lost commit or a hard-failed push. Three workflows already
rebase before pushing; that existing good pattern becomes the shared one every writing workflow uses.

**Blocked by:** 03 — CI runs the test suite. The workflow-configuration assertions need the suite to
live in.

**Status:** ready-for-agent

- [ ] One shared commit-and-push path exists, usable by every workflow that writes to the repo
- [ ] It rebases onto the remote before pushing and retries a rejected push a bounded number of times
- [ ] A push that still cannot land fails the job — never silently skipped
- [ ] Every workflow that commits uses the shared path; none retains its own inline variant
- [ ] Every workflow that commits declares a concurrency group keyed to itself, so two runs of the same
      bot cannot overlap
- [ ] A test at the workflow-configuration seam fails if any writing workflow omits the shared path or
      its concurrency group — so a new bot can't reintroduce the race
- [ ] The three workflows that already rebase behave the same as before
