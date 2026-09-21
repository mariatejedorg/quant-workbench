# Architecture Decision Records

Short, dated records of the decisions that shape this codebase and the alternatives that
were rejected. The format is a light MADR: *Context → Decision → Consequences →
Alternatives considered*. A record is never rewritten after acceptance; a change of mind
is a new record that supersedes the old one.

| # | Decision | Status |
|---|---|---|
| [0001](0001-hexagonal-architecture.md) | Hexagonal architecture with an enforced dependency rule | Accepted |
| [0002](0002-asyncio-core-in-a-worker-thread.md) | asyncio engine in a worker thread, bridged to Qt signals | Accepted |
| [0003](0003-libcst-for-config-editing.md) | Format-preserving config editing with libcst | Accepted |
| [0004](0004-do-not-force-utf8-mode-in-child-processes.md) | Do not force Python's UTF-8 mode in child processes | Accepted |
| [0005](0005-config-edits-are-planned-verified-and-byte-exact.md) | Config edits are planned, verified by read-back and byte-exact | Accepted |
| [0006](0006-the-doctor-is-a-set-of-small-rules-with-previewable-fixes.md) | The doctor is a set of small rules with previewable fixes | Accepted |
| [0007](0007-the-gui-talks-to-a-controller-and-a-command-registry.md) | The GUI talks to a controller and a command registry | Accepted |
| [0008](0008-views-are-lazy-and-never-change-the-project-behind-your-back.md) | Views are lazy, and nothing changes a project behind the user's back | Accepted |
