# 0007. The GUI talks to a controller and a command registry

- Status: Accepted
- Date: 2026-09-21

## Context

The desktop app has many places from which the same action can start: a toolbar button, a
menu entry, a keyboard shortcut, the command palette, a button in a panel, a double-click on
a problem. Left alone, each grows its own copy of "what happens when the user runs a
project" (is a workspace open? is something already running? which engine call?), and the
copies drift. The engine also lives on another thread ([ADR 0002](0002-asyncio-core-in-a-worker-thread.md)),
so every widget that called it directly would need to know about threads.

## Decision

1. **`AppController` is the only thing widgets talk to.** It calls the same use cases as the
   CLI through the `Container`, submits coroutines through the `AsyncBridge`, and answers with
   Qt signals (`catalog_changed`, `events`, `report_ready`, `message`, `busy_changed`).
   Widgets never import the engine and never talk to each other.
2. **One `CommandRegistry` for everything the user can do.** A `Command` has an id, a title, a
   category, a shortcut and an *enabled* predicate. The menus, the toolbar and the palette are
   generated from the registry; the toolbar cannot offer something the palette lacks, and a
   command is greyed out everywhere at once. The registry and the fuzzy matcher are Qt-free
   and unit-tested with plain callables.
3. **Callbacks always arrive on the GUI thread.** `AsyncBridge.submit` delivers `on_result` /
   `on_error` through a queued signal. Engine events go through an `EventRelay` that buffers
   them thread-safely and releases them in batches every 40 ms, so a chatty project costs one
   UI update per tick, not one per line. When a job finishes, the controller flushes the relay
   *before* announcing completion, so a view never says "done" while still showing "running".
4. **Presentation logic that is not drawing is Qt-free and tested without a display.** ANSI
   parsing, the overview page (HTML), the theme tokens (including a WCAG contrast check) and
   the command registry live in modules that do not import PySide6. Only models, widgets and
   the window do; they are tested offscreen with pytest-qt, against the real engine.
5. **The GUI never pushes to git and never edits without a preview.** It has no code path for
   either; the only writes it makes are the ones the use cases expose (config plan/apply,
   doctor fixes, settings).

## Consequences

- Adding a feature is: a use case (tested without Qt), a controller method, a command. Views
  subscribe; they do not orchestrate.
- The controller is a mid-sized class that grows with the app. It is deliberately dumb: it
  translates and forwards, and each method is a few lines.
- Two offscreen-platform quirks are handled in the test fixtures rather than in the app: Qt's
  offscreen plugin on Windows has no fonts (`QT_QPA_FONTDIR`), and its virtual screen is small,
  so tests do not assert window sizes.

## Alternatives considered

- **Widgets call the engine through `qasync`/signals directly** — fewer lines at first,
  threading concerns in every widget, and no single place to fix "busy" or error reporting.
- **A full MVVM framework** — Qt's model/view already provides the view-model layer for the
  three tables; a framework on top would be ceremony.
- **Actions defined per widget** — the classic way to end up with a shortcut that works in the
  menu and not in the palette.
