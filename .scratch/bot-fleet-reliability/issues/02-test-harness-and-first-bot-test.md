# 02 — Test harness and the first end-to-end bot test

**What to build:** A developer can run the whole Python test suite with one command, in seconds, with no
network access and no API keys, and it proves the zenodo bot works end to end. Today there is no way to
find out whether an edit works except waiting for the cron.

The harness drives a bot the way its workflow does — through its entry point — with outbound mail
captured into an in-memory mailbox and outbound HTTP stubbed. It asserts on what the outside world would
observe: the email that would have been sent, the report file written. It never asserts which internal
functions were called.

Standard-library `unittest` discovery, no new test framework dependency — mirroring how `bulbasaur-app/`
already keeps its logic testable with the stdlib runner and no install step.

**Blocked by:** 01 — Shared email sender in `common/`, adopted by the zenodo bot. The mailbox capture
substitutes the shared sender, which must exist first.

**Status:** ready-for-agent

- [ ] One documented command runs the full suite
- [ ] The suite completes without network access and without any API key or credential set
- [ ] An in-memory mailbox captures what the shared sender would have sent, and tests can assert on the
      subject and body
- [ ] Outbound HTTP is stubbed at a single point, so a bot under test cannot reach the internet
- [ ] The zenodo bot is driven through its entry point and asserted on its produced email and report
      output
- [ ] Tests assert on produced artifacts only — no assertions on internal call sequences
- [ ] Adding a test for another bot requires no new harness machinery
