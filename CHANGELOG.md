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
- Study mode: the README sections "Concepts to be able to explain in an interview", "Key findings" and
  "Results" become flash cards scheduled with SM-2 (`qw study`, Study tab); progress is stored in SQLite.
- Doctor: `readme-claims` compares the numbers in a README's Results table with the last run's metrics.
- `qw new <slug>`: generates a project that follows the portfolio's conventions (commented config
  module, simulation, self-contained dashboard, README with the standard sections, manifest with metric
  extractors, TLS bundle inherited from a sibling) and checks it with the doctor.
- `qw sync`: clones the registry's repositories over HTTPS into their local folder names; never touches an
  existing folder; `--setup` builds the environments, `--dry-run` only shows the plan.
- `qw watch <slug>`: re-runs a project whenever a Python file in `src/` or its config folders is saved.
- ADR 0009 records the rules these three commands follow (only add, never overwrite, ignore generated files).

### Fixed
- CI: the `core` matrix installs `.[dev]` only, on purpose, to prove the headless engine needs no Qt —
  but `mypy` was still pointed at the whole package, so it failed there on `ui/`, which needs PySide6's
  stubs to resolve. `core` now type-checks only the headless packages; `gui` (which does install PySide6)
  type-checks the full package, `ui/` included.
- GUI tests on Linux: added `--disable-dev-shm-usage` to the offscreen Chromium flags, a standard
  mitigation for renderer crashes caused by the small `/dev/shm` of CI containers.
- `infrastructure/process.py`: the Windows-only `subprocess.CREATE_NEW_PROCESS_GROUP`/`CREATE_NO_WINDOW`
  flags are looked up with `getattr`, so the module type-checks on Linux too (typeshed's non-Windows
  stub does not define them at all — this only ever surfaced once CI actually ran mypy on Linux).
- `ui/highlight.py`: an installed `types-pygments` stub mistypes `_TokenType.__contains__` as taking a
  `str`; the local venv this was developed in predated that dependency being added and never had it
  installed, so mypy never saw the mismatch until CI's from-scratch install picked it up.
- `qw watch` on Linux misreported every sibling source file as changed after a run, not just the file
  actually edited: watchdog's inotify backend also reports a file merely being *opened for reading*
  (`FileOpenedEvent`/`FileClosedNoWriteEvent`), which the project's own process triggers just by
  importing its source. Windows' backend has no such notion, so this was invisible there. The watcher
  now only reacts to `created`/`modified`/`moved`/`closed` (write) events.
- A project run now also sets `PYTHONDONTWRITEBYTECODE=1`, so it never creates a `src/__pycache__/` —
  unrelated to the bug above (kept as an independent hygiene fix: a run should not litter the project's
  own source folder with bytecode caches it does not need for a script invoked once).
- `test_git_cli.py`: a written pre-commit hook now gets its executable bit set; POSIX git silently
  ignores a non-executable hook (a no-op on Windows, which is why this only failed on Linux).
- `test_views_other.py`: the guarded-commit GUI test now configures both `user.name` and `user.email`
  for the test repository. Only the e-mail is required by policy, but an actual `git commit` still needs
  *some* name; leaving it to git's own auto-detection turned out to behave differently enough between
  Windows and Linux to hang the commit on Linux CI. Confirmed as its own, separate bug from the one
  below: with just this fix, all 105 GUI tests passed on Linux, including this one.
- The GUI test session still crashed on Linux right at process exit, after every test had passed
  ("Release of profile requested but WebEnginePage still not deleted"). Every window built in a test
  builds a `DashboardView`, hence a `QWebEngineView`, whether or not that test ever shows it; left to
  Python's GC and Qt's default parent-child deletion, roughly one hundred of these across the suite were
  torn down in a chaotic order at interpreter exit, which crashes on Linux (invisible on Windows). Each
  test's window is now deleted deterministically, with a short pump of the event loop for the deferred
  deletion — and WebEngine's own asynchronous teardown — to actually run before the next test starts.
  This reduced the crash's rate but did not eliminate it on its own — paired below with a proper Qt
  quit sequence, which addresses why it always happened at exactly this point.
- The GUI test session now shuts Qt down with a real quit sequence at the end (a zero-delay `exec()`
  that triggers `aboutToQuit` and returns immediately) instead of letting the interpreter tear
  everything down ad hoc. `aboutToQuit` is the hook QtWebEngine's global context uses to shut down
  Chromium's browser process in an orderly way — pages before their shared profile — which never fired
  in a pytest session (`exec()` is never otherwise called). Together with the deterministic per-test
  window deletion above, and a longer settle time afterwards for WebEngine's own asynchronous teardown
  (itself IPC to a renderer process), this fixed the segfault: confirmed green on CI, `gui` included,
  on Linux, immediately after this landed. CI is fully green end to end for the first time: `core` on
  both OS across Python 3.11–3.13, `gui` offscreen on Linux, and `build`.
- `core` now also runs on macOS (Python 3.11–3.13), ahead of a teammate who uses one starting to run
  the app. `gui` stays Linux-only for now — nobody on the team currently has a Mac to debug a Qt/
  WebEngine-specific failure there, unlike the headless core, whose test suite already exercises an
  accented workspace path (`Quant - María`) that is exactly the kind of thing macOS's Unicode filename
  normalization (NFD, unlike Windows/Linux) could disagree with.
- The first macOS run immediately found a real one: `qw watch` false-positives again there, but for a
  different reason than the Linux one above. macOS's FSEvents backend does not distinguish a
  metadata-only touch (which a read can cause) from a real write at the *event-type* level — both
  surface as "modified" — so filtering by event type alone, which was enough for Linux, is not enough
  here. `WatchdogChangeSource` now also compares a file's modification time against what it was last
  seen as, but only for "modified" events specifically: applying that same check to renames too
  regressed a real case (a save-via-temp-file-and-rename can legitimately land with a modification time
  that coincides with what was last recorded for the destination path).
- That fix still did not hold up on macOS. The path-resolution theory that followed it was also wrong —
  same failure, byte for byte, after "fixing" it — so the next attempt added temporary diagnostics
  (a stderr print of every event) instead of a third guess, and CI's own log gave the real answer:
  FSEvents replays a "created" event for a file that already existed **before watching started**, up
  to ~30 seconds back, which is documented behaviour of the underlying macOS API. The modification-time
  check now also covers "created" events, not just "modified" — a genuinely new file has no prior
  recorded time to match, so this does not affect real creations. "moved" stays excluded: covering it
  as well reproduced the Windows rename regression from two entries above, intermittently (about 1 run
  in 10 locally), confirming that exclusion was necessary and not a fluke.

