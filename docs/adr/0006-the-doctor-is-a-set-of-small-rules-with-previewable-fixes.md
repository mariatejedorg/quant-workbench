# 0006. The doctor is a set of small rules with previewable fixes

- Status: Accepted
- Date: 2026-09-21

## Context

Every bug found by hand in this portfolio (wrong `config` module picked up from a sibling
project, missing CA bundle behind `CERTIFICATE_VERIFY_FAILED`, a commit authored with the
wrong e-mail, a stale dashboard) was found late and by luck. The workbench should find them
first, tell the user *where*, and where the remedy is mechanical, offer it.

## Decision

1. **One checker per problem class**, a small class with an `id`, a `title` and an async
   `check(project, context)` that returns `Finding` objects. Checkers only talk to ports
   (`ProjectFiles`, `GitGateway`, `EnvironmentService`), so each is unit-tested with in-memory
   fakes and one seeded defect. The pure rules (AST analysis, secrets, complexity) live in
   `domain/code_analysis.py`.
2. **The doctor never crashes because a checker did.** `DoctorService` turns an exception into
   a `checker-crashed` finding and an expected `WorkbenchError` into `checker-unavailable`;
   the other checkers still run.
3. **Severity means something.** `ERROR` = will break a run or violates a hard policy (wrong
   git identity, missing CA bundle, committed credential, syntax error); `WARNING` = worth
   fixing; `INFO` = worth knowing. `qw doctor` exits 1 only on errors.
4. **Fixes are previewed, then applied, and are additive.** A finding may name a fix id;
   `FixService.preview` returns the commands/files/diff without doing anything and `apply`
   does it. The shipped fixes only copy a file, add a line to `.gitignore`, set the repository's
   *own* git identity, or build the environment. The git adapter refuses every config key but
   `user.email`/`user.name` and always uses `--local`, so the global identity of the machine is
   unreachable from the workbench.
5. **Not everything is auto-fixable, on purpose.** A committed `venv/` is an error with the
   command to run in the detail: untracking files changes the repository's content.
6. **Explainability is a number, not an opinion.** The portfolio's non-negotiable rule (the
   author can defend every line) becomes a 0-100 score: docstring coverage 40 %, explanation
   density (comments + docstrings per code line) 25 %, functions of at most 50 lines 20 %,
   complexity at most 10 for 15 %. It is reported per project and only warns below 70.
7. **Opt-in for what has side effects.** The determinism check runs each project twice; it is
   only executed with `--deterministic` (or `--only determinism`).

## Consequences

- On the ten real projects the doctor reports no errors; the only warning is a genuine one
  (documentation coverage of one project below the target). Anything more severe would have
  been a false positive and is a test failure (`test_real_workspace.py`).
- Heuristics can be wrong. The sibling-import rule accepts a `sys.path` insertion only when the
  same function also touches `sys.modules`; a cleaner in a helper function would be flagged.
  That is accepted: the finding says exactly what to look at.
- The static-analysis checker is deliberately small (bare `except`, mutable defaults, unused
  imports, syntax errors); it does not replace ruff and needs nothing installed in the
  project's environment.

## Alternatives considered

- **Run ruff/flake8 inside each project's venv** — more rules, but depends on the tool being
  installed there and adds seconds per project; the bugs we care about are not style.
- **Auto-fix everything** — a fix that rewrites history or deletes files is a bug with better
  marketing; the ones that are safe are offered, the rest are explained.
- **Fail the whole doctor on the first problem** — hides the second and third; users want the
  full list.
