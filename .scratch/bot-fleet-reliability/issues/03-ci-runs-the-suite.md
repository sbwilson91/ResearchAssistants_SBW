# 03 — CI runs the test suite, and guards against real network calls

**What to build:** Opening a pull request runs the test suite automatically, and a change that breaks a
bot fails there rather than at 07:00 on a Monday. A developer can no longer merge a break, and the suite
cannot silently start depending on the internet.

This is the point at which the safety net becomes real: everything after this ticket is verified by CI
rather than by the next cron.

**Blocked by:** 02 — Test harness and the first end-to-end bot test.

**Status:** ready-for-agent

- [ ] A CI job runs the Python test suite on pull requests and on pushes to the main branch
- [ ] The job installs only what the tests need, and does not install the heavy ML stack
- [ ] A failing test fails the job
- [ ] A test that attempts a real outbound network call fails, with a message naming the offending call
- [ ] The job does not require any repository secret to pass
- [ ] The bots' scheduled workflows are unaffected by this job
