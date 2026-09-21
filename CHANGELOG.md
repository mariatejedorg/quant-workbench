# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Project foundations: hexagonal package layout, tooling (ruff, mypy strict, pytest), CI, ADRs.
- Project catalog: `quant-project.toml` manifest (pydantic v2, JSON Schema export), registry of the
  ten portfolio projects, discovery with in-repo > registry > inferred precedence.
- Dependency graph: cycle detection, parallel execution layers, transitive impact analysis; edges
  are the union of declared dependencies and those detected by static analysis of the source.
- CLI: `qw list`, `qw graph` (text / mermaid / json / `--impact`), `qw schema`.
- Job engine: asyncio process runner (process-tree kill on timeout/cancel, CPU/RSS sampling), DAG-aware
  batches with bounded concurrency, retries with backoff for transient failures, run history in SQLite
  (versioned migrations), metric extractors and a data-driven failure-signature knowledge base.
- Environments: `pip list`-based inspection of a project's venv, venv creation and requirements install.
- CLI: `qw run`, `qw run-all`, `qw env status|setup`, `qw history`, `qw logs`.
- Config editor (libcst): lists a project's literal constants with type, value and the comment above
  them as help text; edits preserve comments, layout and Windows line endings byte for byte, merge
  dict/list/tuple edits element by element, verify every edit by reading it back, and keep a backup
  outside the project. Property-based tests run against the ten real config files.
- CLI: `qw config get | diff | set`.
- Doctor: checkers for the virtual environment, TLS certificate bundle, sibling-import
  collisions, sibling references vs declared dependencies, git identity policy, repository
  hygiene (uncommitted work, tracked venv, large files, committed secrets), generated-output
  freshness, static analysis and an explainability score; opt-in determinism check. Findings
  carry severity, location and, where safe, a previewable fix (`copy-ca-bundle`,
  `setup-environment`, `set-git-identity`, `ignore-venv`).
- Git adapter over the `git` CLI; it can only ever write the repository's own identity.
- CLI: `qw doctor [--fix] [--json] [--only/--skip] [--deterministic] [--list]`.
- Desktop app (PySide6): main window with project explorer (run-status badges), overview,
  console with ANSI colours and per-project filter, job queue, problems panel, command
  palette (Ctrl+K), settings dialog, light/dark theme from the portfolio palette, persistent
  layout. The engine runs on its own thread behind an `AsyncBridge`; events reach the GUI in
  batches. Launch with `qw gui` or `quant-workbench`.
- Settings can be saved from the app (`settings.toml`, written atomically).
- Functional GUI: configuration form (typed editors, help from the comments, diff review, undo/redo
  that refuses to overwrite outside edits), dashboard viewer (embedded Chromium, auto-reload, local
  Plotly cache, PDF/PNG export), code viewer/editor (Pygments highlighting, outline, search, guarded
  editing with backups), README viewer, run history (log, two-run comparison of config/metrics/outputs,
  metric trend chart), dependency graph with impact analysis and "re-run impacted", Git panel
  (status, diff, log, guarded commit, no push), and fixes from the Problems panel with a preview.
- Run comparison, metric series, graph layout and a guarded git commit use case as tested Qt-free code.
- Fixed: subprocess transports are closed explicitly after a kill or timeout.

