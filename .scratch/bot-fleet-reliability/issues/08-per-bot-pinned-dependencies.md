# 08 — Per-bot pinned dependency sets

**What to build:** Each bot installs only what it needs, at exact versions. The citation bot stops
installing the ML stack to make a handful of HTTP calls, its job gets substantially faster, and a
scheduled run behaves the same this week as last week. A dependency upgrade becomes a deliberate,
reviewable change rather than something that arrives at 07:00 on a Monday.

The single flat requirements file is replaced by a shared base for what `common/` needs plus one set per
bot. The heavy ML stack is confined to the journal digest, which is its only consumer.

**Blocked by:** 07 — Safe concurrent publishing. Both edit every workflow; sequencing them avoids
conflicting rewrites of the same files.

**Status:** ready-for-agent

- [ ] A shared base dependency set covers what `common/` needs
- [ ] Each bot has its own dependency set declaring only what that bot needs, layered on the base
- [ ] Every dependency is pinned to an exact version
- [ ] The ML stack appears only in the journal digest's set
- [ ] Each workflow installs the base plus its own bot's set, and nothing else
- [ ] Every bot still runs correctly with only its own set installed
- [ ] A test at the workflow-configuration seam fails if a workflow installs a set other than its own
- [ ] The old flat requirements file is removed, and the documented way to add a dependency names the
      per-bot sets
