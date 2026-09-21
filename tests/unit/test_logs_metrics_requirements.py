from __future__ import annotations

import pytest

from quant_workbench.domain.logs import LogClassifier, strip_ansi, summarize_failure
from quant_workbench.domain.metrics import extract_metrics
from quant_workbench.domain.project import MetricExtractorSpec, MetricKind, MetricPick
from quant_workbench.domain.requirements import (
    missing_requirements,
    normalize_name,
    parse_requirements,
)
from quant_workbench.domain.runs import LogLevel, LogStream, MetricValue, RunStatus

OUT, ERR, SYS = LogStream.STDOUT, LogStream.STDERR, LogStream.SYSTEM


# ------------------------------------------------------------------- classifier
def test_a_whole_traceback_block_is_marked_as_error() -> None:
    classifier = LogClassifier()
    traceback = [
        (ERR, "Traceback (most recent call last):"),
        (ERR, '  File "main.py", line 3, in <module>'),
        (ERR, "    main()"),
        (ERR, "KeyError: 'x'"),
    ]
    assert [classifier.classify(s, t) for s, t in traceback] == [LogLevel.ERROR] * 4
    # the block is closed by the exception line: the next ordinary line is not an error
    assert classifier.classify(OUT, "done") is LogLevel.INFO


def test_warnings_stderr_errors_and_system_lines() -> None:
    classifier = LogClassifier()
    assert classifier.classify(ERR, "ValueError: bad") is LogLevel.ERROR
    assert classifier.classify(OUT, "DeprecationWarning: old api") is LogLevel.WARNING
    assert classifier.classify(OUT, "Warning: something") is LogLevel.WARNING
    assert classifier.classify(ERR, "just some progress") is LogLevel.INFO
    assert classifier.classify(SYS, "ValueError in a system message") is LogLevel.INFO
    assert classifier.classify(OUT, "\x1b[31mred text\x1b[0m") is LogLevel.INFO


def test_strip_ansi_removes_colour_and_cursor_codes() -> None:
    assert strip_ansi("\x1b[1;32mOK\x1b[0m \x1b[2K done") == "OK  done"


def test_summarize_failure_takes_the_last_exception_line() -> None:
    lines = ["noise", "KeyError: 'a'", "more noise", "ValueError: the real one", "trailing"]
    assert summarize_failure(lines) == "ValueError: the real one"
    assert summarize_failure(["all fine"]) is None
    assert summarize_failure(["yfinance.exceptions.YFRateLimitError: Too Many Requests"]) == (
        "yfinance.exceptions.YFRateLimitError: Too Many Requests"
    )


# ---------------------------------------------------------------------- metrics
def spec(name: str, pattern: str, kind: MetricKind = MetricKind.FLOAT) -> MetricExtractorSpec:
    return MetricExtractorSpec(name, pattern, kind)


def test_last_match_wins_because_summaries_come_last() -> None:
    lines = ["Sharpe: 1.1", "working...", "Sharpe: 2.45"]
    assert extract_metrics(lines, [spec("sharpe", r"Sharpe:\s*([\d.]+)")]) == (
        MetricValue("sharpe", 2.45),
    )


@pytest.mark.parametrize(
    ("line", "kind", "expected"),
    [
        ("retorno +38.22% anual", MetricKind.PERCENT, 38.22),
        ("drawdown -10.09%", MetricKind.PERCENT, -10.09),
        ("cap 54,330,990,963 USD", MetricKind.INT, 54_330_990_963),
        ("valor: 1,234.5", MetricKind.FLOAT, 1234.5),
        ("delta −3.5", MetricKind.FLOAT, -3.5),  # noqa: RUF001 - the typographic minus is the point of this case
        ("ticker: AAPL", MetricKind.STR, "AAPL"),
        ("x = 1e-3", MetricKind.FLOAT, 0.001),
    ],
)
def test_value_parsing(line: str, kind: MetricKind, expected: float | int | str) -> None:
    pattern = r"(?:ticker: |retorno |drawdown |cap |valor: |delta |x = )(\S+)"
    (metric,) = extract_metrics([line], [spec("m", pattern, kind)])
    assert metric.value == expected


def test_pick_first_keeps_the_first_match_instead_of_the_last() -> None:
    lines = ["PER: 8.7", "later section reuses the row", "PER: 0.06"]
    last = MetricExtractorSpec("per", r"PER:\s*([\d.]+)")
    first = MetricExtractorSpec("per", r"PER:\s*([\d.]+)", pick=MetricPick.FIRST)

    assert extract_metrics(lines, [last])[0].value == 0.06
    assert extract_metrics(lines, [first])[0].value == 8.7


def test_metrics_that_do_not_match_or_are_not_numbers_are_simply_absent() -> None:
    lines = ["Sharpe: n/a", "nothing here"]
    extractors = [spec("sharpe", r"Sharpe:\s*(\S+)"), spec("missing", r"Never: (\d+)")]
    assert extract_metrics(lines, extractors) == ()


def test_a_bad_line_does_not_erase_an_earlier_good_value() -> None:
    lines = ["Sharpe: 1.5", "Sharpe: n/a"]
    assert extract_metrics(lines, [spec("s", r"Sharpe:\s*(\S+)")])[0].value == 1.5


def test_ansi_codes_do_not_break_extraction() -> None:
    lines = ["\x1b[32mSharpe: 2.0\x1b[0m"]
    assert extract_metrics(lines, [spec("s", r"Sharpe:\s*([\d.]+)")])[0].value == 2.0


# ----------------------------------------------------------------- requirements
def test_requirements_parsing_handles_the_real_world_formats() -> None:
    text = """
# a comment
numpy
pandas>=2.0,<3   # inline comment
Scikit_Learn[extra]==1.5 ; python_version < "3.13"
-r other.txt
--index-url https://example.org/simple
git+https://github.com/x/y.git#egg=y
./local/path
requests @ https://example.org/requests.zip
matplotlib
"""
    parsed = parse_requirements(text)

    assert [r.name for r in parsed] == ["numpy", "pandas", "scikit-learn", "matplotlib"]
    assert parsed[1].specifier == ">=2.0,<3"
    assert parsed[2].specifier == "==1.5"


def test_names_are_normalised_per_pep_503() -> None:
    assert normalize_name("Scikit_Learn") == normalize_name("scikit-learn") == "scikit-learn"
    assert normalize_name("ruamel.yaml") == "ruamel-yaml"


def test_missing_requirements_compare_by_normalised_name() -> None:
    required = parse_requirements("numpy\nScikit_Learn\nyfinance\n")
    installed = {"NumPy": "2.0", "scikit-learn": "1.5"}

    assert [r.name for r in missing_requirements(required, installed)] == ["yfinance"]


def test_run_status_helpers() -> None:
    assert RunStatus.SUCCEEDED.is_terminal
    assert RunStatus.SUCCEEDED.is_success
    assert not RunStatus.RUNNING.is_terminal
    assert RunStatus.SKIPPED.is_terminal
    assert not RunStatus.FAILED.is_success
