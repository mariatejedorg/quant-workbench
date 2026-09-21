# 0004. Do not force Python's UTF-8 mode in child processes

- Status: Accepted
- Date: 2026-09-21

## Context

Child processes (the projects) print accented Spanish text and must stay readable when
their output is captured through a pipe on Windows, where the default code page is
cp1252. The obvious switch is `PYTHONUTF8=1`, which the first version of the process
runner set on every child.

With it, running the whole portfolio through the workbench failed for four of ten
projects: every Yahoo Finance request logged `Cookie/crumb fetch failed (SSLError)` and
then `possibly delisted; no price data found`. The same projects ran fine from a terminal.

Bisecting the differences between the workbench's launcher and a terminal (inherited
environment, sanitised environment, creation flags, `stdin`, extra variables) isolated one
variable: **`PYTHONUTF8=1` alone reproduced the failure**.

The cause is an encoding mismatch at a C boundary. In UTF-8 mode Python passes file paths
to native libraries encoded as UTF-8. libcurl (used by `curl_cffi`, hence `yfinance`) reads
paths on Windows in the ANSI code page. The workspace is called `Quant - María`, so the
path of each project's CA bundle (`.certs/cacert.pem`) no longer resolved, and every TLS
handshake failed. Only projects that passed the custom session to `yf.Ticker(...)` were
affected; the ones that used `yf.download(...)` happened to take a different code path.

## Decision

The runner forces `PYTHONIOENCODING=utf-8` (which makes only the standard streams UTF-8,
enough for accented output through pipes) and **never sets `PYTHONUTF8`**. A regression
test asserts that a child started by the runner reports `sys.flags.utf8_mode == 0`.

The failure is also captured as a *failure signature* (`tls-setup-failed`) that is listed
before the misleading `yfinance-no-data` one, so the run history shows the root cause
instead of the symptom.

## Consequences

- Reading a file with `open()` and no explicit encoding still uses the locale code page in
  children, exactly as it did when the projects were run by hand. That is the environment
  the projects were written and validated in, so it is the safer default than a "better"
  one they never saw.
- The doctor (milestone M4) should warn when the workspace path is non-ASCII and
  `PYTHONUTF8=1` is present in the user's environment, since that combination breaks the
  same way outside the workbench.

## Alternatives considered

- **Keep `PYTHONUTF8=1` and move the workspace to an ASCII path** — correct, but it shifts
  the cost to every user with an accented username or folder (common in Spanish-speaking
  locales) instead of removing the cause.
- **Decode the child's output ourselves with cp1252** — fragile: it would have to guess
  each program's encoding, and would corrupt output from programs that already emit UTF-8.
- **Patch each project's `_build_session()`** — the projects are finished and presented as
  they are; a tool that requires changing them defeats the purpose of running them
  unmodified.
