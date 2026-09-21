# 0008. Views are lazy, and nothing changes a project behind the user's back

- Status: Accepted
- Date: 2026-09-21

## Context

The functional GUI adds seven views over the selected project (configuration, dashboard, code,
README, history, dependency graph, Git). Some are expensive (the Git tab shells out to git,
the dashboard loads a Chromium page) and some can modify the user's files (the configuration
form, the code editor, the commit button). The projects are the user's own repositories.

## Decision

1. **A view is brought up to date only when it is on screen.** `ProjectViews` owns the tabs;
   selecting another project or finishing a run marks views *stale*, and a stale view refreshes
   when its tab is shown (immediately if it already is). Ten projects times seven views are
   never all loaded at once.
2. **Every write goes through a use case with a preview and a guard, never straight from a
   widget.**
   - Configuration: `ConfigService.plan` → the diff dialog → `apply`. The plan remembers the
     text it was computed from and refuses to write if the file changed meanwhile; *undo* is the
     inverse plan, with the same guard, so an edit made elsewhere is never rolled back over.
   - Code editor: read-only until "Allow editing" is ticked; saving refuses a Python file that
     does not parse and a file that changed since it was opened, and keeps a backup outside the
     project.
   - Git: commits require the portfolio identity; there is no push, only the command to copy.
   - Doctor fixes: previewed (commands, files, diff) before "Apply".
3. **Blocking work never runs on the GUI thread.** Git calls and Plotly downloads go through
   `AppController.run_blocking` (a worker thread behind the `AsyncBridge`); results are matched
   to the selection that asked for them and dropped if the user moved on.
4. **Dashboards are displayed from a copy, the original is untouched.** The dashboards load
   Plotly from a CDN. The viewer downloads that script once into the cache folder and shows a
   copy of the HTML whose `<script src>` points at it, so the dashboard works offline; offline
   on the very first view it falls back to the CDN URL. The project's `dashboard.html` is never
   rewritten. Only `https://` URLs are ever fetched.
5. **README and other project text is data.** Markdown is rendered with raw HTML disabled.

## Consequences

- Tab switches are instant after the first load and a full "run all" does not hammer git.
- Because writes are plan/apply everywhere, the CLI (`qw config set`, `qw doctor --fix`) and the
  GUI share their safety properties and their tests.
- The cost is some ceremony (a stale set, stale-answer tokens) that is invisible to the user.
- The embedded browser needs `PySide6-Addons`; without it the dashboard tab says so instead of
  failing (the rest of the app is unaffected).

## Alternatives considered

- **Refresh every view on every selection** — simple, and slow: seven git/browser calls per
  click.
- **Let the editor write directly** — fewer lines; one bad save away from a broken project.
- **Serve dashboards through a local web server with a request interceptor** — more moving
  parts than rewriting one `<script>` attribute in a copy.
