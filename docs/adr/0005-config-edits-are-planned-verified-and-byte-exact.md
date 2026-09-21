# 0005. Config edits are planned, verified by read-back and byte-exact

- Status: Accepted
- Date: 2026-09-21

## Context

[ADR 0003](0003-libcst-for-config-editing.md) chose libcst and the "replace only the value
node" strategy. Implementing it against the real portfolio exposed the details that decide
whether the promise holds:

- The real config files use **Windows line endings** (`\r\n`). `Path.read_text` silently
  turns them into `\n` and `Path.write_text` turns them back on Windows only, so a naive
  round trip either rewrites every line or corrupts the file depending on the platform.
- Two projects keep their parameters in a **dict** (`TICKERS`, `COMPANIES`), one entry per
  line and often with a trailing comment. Replacing the whole dict on every edit would
  discard those comments and reflow the file, even when the user only added one ticker.
- `0.90` and `0.9` are the same number. Setting a constant to the value it already has must
  not produce a diff.
- A bug in the rewriting logic would be invisible to the user until their project stopped
  working, and the file lives in a repository the workbench does not own.

## Decision

1. **Two steps: plan, then apply.** `ConfigService.plan` computes the new text of every
   affected file and a unified diff without touching the disk; `apply` writes it. The CLI
   prints the diff before writing and the GUI will show it in a dialog: same code path.
   `apply` refuses if a file changed since it was planned, so an edit made in another editor
   in the meantime is never silently overwritten.
2. **Bytes in, bytes out.** Files are read and written through the `ProjectFiles` port with
   no newline translation; the editor sees `\r\n` and preserves it. Writes go to a temporary
   file next to the target followed by an atomic `Path.replace`, and the previous version is
   copied to the workbench's backup folder (`<data>/backups/<project>/<timestamp>/`), never
   inside the project.
3. **Containers are merged element by element.** For a dict, list or tuple the transformer
   keeps every unchanged element node untouched (so its comment, spacing and comma survive),
   replaces only the values that differ, and appends new entries cloned from the last one.
   Commas are reassigned only where an element changes position class (last vs. not last).
   A comment on its own line above an entry travels with the *previous* entry's comma in
   libcst's tree; removing that previous entry therefore removes the comment (documented
   trade-off: the alternative is a much larger rewriting engine).
4. **Read-back verification.** After every edit the result is parsed again: each requested
   constant must read back *exactly* as asked (`1`, `1.0` and `True` are different values)
   and every other constant must be unchanged. Otherwise `UnsafeEditError` is raised and
   nothing is returned to be written. This turns the correctness of the rewriting into a
   checked invariant instead of an argument.
5. **Strict types, small conveniences.** A constant keeps its type (`int` stays `int`);
   only `int → float` and `list → tuple` are accepted implicitly. `nan`/`inf` and anything
   with no literal form are refused. Ambiguous names (assigned twice) and computed
   constants are listed as non-editable, with the reason given when someone asks for them.
6. **Style follows the original.** New values reuse the quote character, digit grouping
   (`10_000`) and parentheses of the value they replace, and a float's exponent is written
   the short way (`1e-6`, not `1e-06`).

## Consequences

- A real edit shows up in `git diff` as exactly the changed line(s), verified on all ten
  real files with both line endings (property tests, 150 random edits per run).
- Snapshots of the configuration stored with each run are now exact bytes, so comparing two
  runs' configuration is a true diff.
- The editor's correctness rests on `ast.literal_eval` agreeing with libcst's tree, and on
  the read-back check catching the cases where they disagree.
- Cost: `constants()` parses the file three times per edit (list, rewrite, verify). At
  configuration-file size that is milliseconds, and it keeps each step independently
  testable.

## Alternatives considered

- **Replace the whole container on every edit** — simplest, but rewrites every line of a
  ten-entry dict and loses its comments for a one-entry change.
- **Trust the transformer, test only** — tests cover the cases we thought of; the read-back
  check covers the ones we did not, at negligible cost.
- **`.bak` files next to the source** — they would show up in the user's `git status`.
- **Universal newlines everywhere** — would normalise the files to the platform's ending,
  producing whole-file diffs the first time a config is edited.
