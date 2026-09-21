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
