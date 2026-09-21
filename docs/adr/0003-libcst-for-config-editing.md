# 0003. Format-preserving config editing with libcst

- Status: Accepted
- Date: 2026-09-21

## Context

Each project keeps its parameters as module-level constants in a `config/*.py` file
(or, in two older projects, inside `src/data.py`), documented by hand-written comments:

```python
# Number of paths used by the Monte Carlo simulation.
N_SIMULATIONS = 10_000
```

The workbench lets the user edit these values from a typed form. The files are the
user's own source code, in version control, and the comments *are* the documentation. An
editor that reformats the file, drops a comment or reorders keys is worse than no editor.

## Decision

Introspect and rewrite constants with **libcst** (a concrete syntax tree that retains
every whitespace character and comment):

1. Parse the module and collect assignments whose value is a literal (int, float, str,
   bool, None, tuple, list, dict). The comment block directly above an assignment becomes
   the parameter's help text.
2. Apply an edit with a `CSTTransformer` that replaces **only** the value node, so the
   bytes outside it are provably untouched. Show a unified diff before saving.
3. If a value cannot be rewritten safely (non-literal expression, computed value, a
   construct the transformer does not handle) the editor **refuses** with
   `UnsafeEditError` and offers the manual editor. It never guesses.
4. Property-based tests (hypothesis) assert *parse -> edit -> serialise* changes nothing
   outside the edited node, run against every real config file in the portfolio.

## Consequences

- Edits are reviewable in `git diff` as a single-line change.
- The safety guarantee is testable and tested, not asserted.
- Cost: libcst is heavier than `ast` and slower to parse; irrelevant at config-file size.

## Alternatives considered

- **`ast.parse` + `ast.unparse`** — loses comments and normalises formatting; rejected
  outright, it destroys the documentation the form is built from.
- **Regex substitution** — works for `NAME = 5` and fails on multi-line dicts, strings
  containing `#`, and continuation lines; the failure mode is silent corruption.
- **Moving config to TOML/JSON** — cleaner, but requires rewriting ten finished projects
  that the portfolio presents as-is; the tool should adapt to the code, not the reverse.
- **`tokenize`-based patching** — precise but re-implements what libcst already does.
