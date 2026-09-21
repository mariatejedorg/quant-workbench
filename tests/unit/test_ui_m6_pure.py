"""Qt-free logic behind the functional views: form state, diffs, highlighting, caches."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pygments.token import Token

from quant_workbench.application.config import ProjectConstant
from quant_workbench.domain.code_analysis import outline, parse
from quant_workbench.domain.config import Constant
from quant_workbench.domain.history import compare_runs
from quant_workbench.domain.literals import ValueKind
from quant_workbench.domain.runs import JobKind, MetricValue, OutputFileState, Run, RunStatus
from quant_workbench.infrastructure.config_editor import LibCstConfigEditor
from quant_workbench.ui.config_form import ConfigFormState, display_text, wants_multiline_editor
from quant_workbench.ui.diffhtml import diff_html
from quant_workbench.ui.highlight import (
    MAX_HIGHLIGHTED_CHARS,
    category_of,
    spans_by_line,
    syntax_colours,
)
from quant_workbench.ui.history_html import comparison_html
from quant_workbench.ui.markdown import render_markdown, stylesheet
from quant_workbench.ui.plotly_cache import PlotlyCache, download, find_cdn_scripts
from quant_workbench.ui.theme import DARK, LIGHT, contrast_ratio

SOURCE = (
    '"""Doc."""\n'
    "# the ticker\n"
    'TICKER = "AAPL"\n'
    "WINDOW = 252  # sessions\n"
    "CAPITAL = 10_000.0\n"
    "FLAG = True\n"
    "RANGE = (0.7, 1.3)\n"
    "HOLDINGS = {\n"
    '    "A": {"w": 0.5},\n'
    '    "B": {"w": 0.5},\n'
    "}\n"
)


def entries(source: str = SOURCE) -> list[ProjectConstant]:
    return [ProjectConstant("config/a.py", c) for c in LibCstConfigEditor().constants(source)]


def form() -> ConfigFormState:
    return ConfigFormState(entries())


# --------------------------------------------------------------------- form state
def test_the_form_shows_values_the_way_a_person_reads_them() -> None:
    by_name = {f.constant.name: f.text for f in form().fields}

    assert by_name["TICKER"] == "AAPL"  # no quotes: the type is fixed
    assert by_name["FLAG"] == "true"
    assert by_name["CAPITAL"] == "10_000.0"  # exactly as written
    assert by_name["RANGE"] == "(0.7, 1.3)"
    assert by_name["HOLDINGS"].startswith("{\n")  # multi-line, with Unix newlines


def test_display_text_normalises_windows_newlines() -> None:
    constant = LibCstConfigEditor().constants("D = {\r\n    1: 2,\r\n}\r\n")[0]

    assert "\r" not in display_text(constant)


def test_multiline_editors_are_for_long_or_multiline_containers_only() -> None:
    by_name = {f.constant.name: f.constant for f in form().fields}

    assert wants_multiline_editor(by_name["HOLDINGS"])
    assert not wants_multiline_editor(by_name["RANGE"])
    assert not wants_multiline_editor(by_name["TICKER"])
    long_tuple = LibCstConfigEditor().constants(f"T = ({', '.join(['1.5'] * 30)})\n")[0]
    assert wants_multiline_editor(long_tuple)


def test_a_fresh_form_is_clean() -> None:
    state = form()

    assert state.dirty_keys == ()
    assert state.invalid_keys == ()
    assert not state.can_save
    assert state.changes() == {}


def test_editing_a_field_makes_it_dirty_and_produces_a_typed_change() -> None:
    state = form()

    state.set_text("config/a.py:WINDOW", "126")
    state.set_text("config/a.py:TICKER", "MSFT")
    state.set_text("config/a.py:FLAG", "false")
    state.set_text("config/a.py:RANGE", "(0.5, 1.5)")

    assert state.can_save
    assert state.changes() == {
        "config/a.py:WINDOW": 126,
        "config/a.py:TICKER": "MSFT",
        "config/a.py:FLAG": False,
        "config/a.py:RANGE": (0.5, 1.5),
    }


def test_retyping_the_original_value_is_not_a_change() -> None:
    state = form()

    state.set_text("config/a.py:WINDOW", "999")
    assert state.dirty_keys == ("config/a.py:WINDOW",)
    state.set_text("config/a.py:WINDOW", "252")
    state.set_text("config/a.py:CAPITAL", "10000.0")  # same number, spelled differently

    assert state.dirty_keys == ()


def test_invalid_text_is_reported_and_blocks_saving() -> None:
    state = form()

    state.set_text("config/a.py:WINDOW", "many")
    state.set_text("config/a.py:TICKER", "MSFT")

    field = state.field("config/a.py:WINDOW")
    assert field.error is not None
    assert "not a valid int" in field.error
    assert state.invalid_keys == ("config/a.py:WINDOW",)
    assert not state.can_save  # one bad field blocks the whole save
    assert "config/a.py:WINDOW" not in state.changes()
    assert not field.is_dirty  # an invalid field is not a pending change


def test_fixing_the_text_clears_the_error() -> None:
    state = form()
    state.set_text("config/a.py:WINDOW", "x")

    state.set_text("config/a.py:WINDOW", "10")

    assert state.field("config/a.py:WINDOW").error is None
    assert state.can_save


def test_reset_discards_every_edit() -> None:
    state = form()
    state.set_text("config/a.py:WINDOW", "1")
    state.set_text("config/a.py:TICKER", "X")

    state.reset()

    assert state.dirty_keys == ()
    assert state.field("config/a.py:WINDOW").text == "252"


def test_container_types_must_match_but_a_list_is_accepted_for_a_tuple() -> None:
    state = form()

    state.set_text("config/a.py:RANGE", "{'a': 1}")  # a dict where a tuple lives
    assert state.field("config/a.py:RANGE").error is not None

    state.set_text("config/a.py:RANGE", "[0.5, 1.5]")  # what JSON would give: read as a tuple
    assert state.field("config/a.py:RANGE").error is None
    assert state.changes() == {"config/a.py:RANGE": (0.5, 1.5)}


def test_a_constant_of_kind_none_accepts_any_literal() -> None:
    constant = Constant("X", ValueKind.NONE, None, "None", "", 1, 1)
    state = ConfigFormState([ProjectConstant("a.py", constant)])

    state.set_text("a.py:X", "'value'")

    assert state.changes() == {"a.py:X": "value"}


# -------------------------------------------------------------------------- diffs
def test_diff_lines_are_coloured_by_kind_and_escaped() -> None:
    diff = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-A = <1>\n+A = <2>\n context\n"

    html = diff_html(diff, LIGHT)

    assert f"color:{LIGHT.success}'>+A = &lt;2&gt;" in html
    assert f"color:{LIGHT.error}'>-A = &lt;1&gt;" in html
    assert f"color:{LIGHT.accent}'>@@ -1 +1 @@" in html
    assert f"color:{LIGHT.ink_muted}'>--- a/f" in html
    assert "<1>" not in html  # never raw


def test_an_empty_diff_says_so() -> None:
    assert "No differences" in diff_html("  \n", LIGHT)


def test_carriage_returns_do_not_leak_into_the_html() -> None:
    assert "\r" not in diff_html("-A = 1\r\n+A = 2\r\n", LIGHT)


# -------------------------------------------------------------------- comparison
T0 = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def make_run(n: int, **kwargs: object) -> Run:
    return Run(
        id=f"r{n}",  # type: ignore[arg-type]
        project="alpha",  # type: ignore[arg-type]
        kind=JobKind.RUN,
        status=RunStatus.SUCCEEDED,
        queued_at=T0 + timedelta(minutes=n),
        started_at=T0 + timedelta(minutes=n),
        finished_at=T0 + timedelta(minutes=n, seconds=5),
        **kwargs,  # type: ignore[arg-type]
    )


def test_the_comparison_page_lists_config_metrics_and_outputs() -> None:
    before = make_run(
        1,
        config_snapshot=(("c.py", "N = 5\n"),),
        metrics=(MetricValue("sharpe", 1.0), MetricValue("ticker", "A<b>")),
        outputs=(OutputFileState("outputs/a.png", 1, "aa"),),
    )
    after = make_run(
        2,
        config_snapshot=(("c.py", "N = 7\n"),),
        metrics=(MetricValue("sharpe", 1.5), MetricValue("ticker", "B")),
        outputs=(OutputFileState("outputs/a.png", 1, "bb"),),
    )

    html = comparison_html(compare_runs(before, after), LIGHT)

    assert "r1 &rarr; r2" in html
    assert "+N = 7" in html
    assert "sharpe" in html
    assert "+0.5" in html  # the delta
    assert "A&lt;b&gt;" in html  # text values are escaped
    assert "outputs/a.png" in html
    assert "changed" in html


def test_identical_runs_produce_reassuring_messages() -> None:
    same = make_run(1, config_snapshot=(("c.py", "N = 5\n"),), metrics=(MetricValue("m", 1.0),))

    html = comparison_html(
        compare_runs(same, make_run(2, config_snapshot=same.config_snapshot, metrics=same.metrics)),
        LIGHT,
    )

    assert "configuration is identical" in html
    assert "No metric changed" in html
    assert "Every output file is identical" in html


# ---------------------------------------------------------------------- highlight
def test_tokens_are_categorised() -> None:
    assert category_of(Token.Keyword) == "keyword"
    assert category_of(Token.Literal.String.Double) == "string"
    assert category_of(Token.Literal.String.Doc) == "comment"  # docstrings read as commentary
    assert category_of(Token.Comment.Single) == "comment"
    assert category_of(Token.Literal.Number.Integer) == "number"
    assert category_of(Token.Name.Function) == "function"
    assert category_of(Token.Name.Builtin) == "builtin"
    assert category_of(Token.Name.Decorator) == "decorator"
    assert category_of(Token.Name) is None
    assert category_of(Token.Text) is None


def test_spans_are_cut_per_line_with_columns() -> None:
    spans = spans_by_line('x = 1  # note\ndef f():\n    return "s"\n', "a.py")

    assert (4, 1, "number") in spans[0]
    assert (7, 6, "comment") in spans[0]
    assert (0, 3, "keyword") in spans[1]
    assert any(category == "string" for _, _, category in spans[2])


def test_a_multiline_docstring_is_coloured_on_every_line() -> None:
    spans = spans_by_line('"""one\ntwo\nthree"""\nx = 1\n', "a.py")

    assert all(any(c == "comment" for _, _, c in line) for line in spans[:3])
    assert not any(c == "comment" for _, _, c in spans[3])


def test_unknown_file_types_and_huge_files_are_left_plain() -> None:
    assert spans_by_line("hello world\n", "notes.zzz") == [[], []]
    huge = "x = 1\n" * (MAX_HIGHLIGHTED_CHARS // 6 + 10)
    lines = spans_by_line(huge, "a.py")
    assert len(lines) == huge.count("\n") + 1
    assert all(not line for line in lines)


def test_syntax_colours_are_legible_on_the_editor_background() -> None:
    for tokens in (LIGHT, DARK):
        colours = syntax_colours(tokens)
        for category in (
            "keyword",
            "string",
            "comment",
            "number",
            "function",
            "builtin",
            "decorator",
        ):
            assert contrast_ratio(colours.of(category), tokens.console_bg) >= 4.5, (
                tokens.name,
                category,
            )


# ------------------------------------------------------------------------ outline
def test_the_outline_lists_classes_functions_and_methods_but_not_nested_defs() -> None:
    tree = parse(
        "def top():\n    def inner():\n        pass\n\n\nclass K:\n    def m(self):\n        pass\n\n"
        "    class Inner:\n        def deep(self):\n            pass\n\n\nasync def later():\n    pass\n"
    )
    assert tree is not None

    symbols = [(s.name, s.kind, s.depth) for s in outline(tree)]

    assert symbols == [
        ("top", "function", 0),
        ("K", "class", 0),
        ("m", "method", 1),
        ("Inner", "class", 1),
        ("deep", "method", 2),
        ("later", "function", 0),
    ]


# ------------------------------------------------------------------------ markdown
def test_markdown_renders_tables_and_refuses_raw_html() -> None:
    html = render_markdown("# T\n\n| a | b |\n|--|--|\n| 1 | 2 |\n\n<script>alert(1)</script>\n")

    assert "<h1>T</h1>" in html
    assert "<table>" in html
    assert "<script>" not in html  # shown as text, never interpreted
    assert "&lt;script&gt;" in html


def test_the_readme_stylesheet_follows_the_theme() -> None:
    assert DARK.accent in stylesheet(DARK)
    assert LIGHT.window in stylesheet(LIGHT)


# --------------------------------------------------------------------- plotly cache
HTML = (
    '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>\n'
    "<script src='https://cdn.plot.ly/plotly-2.35.2.min.js'></script>\n"
    '<script src="https://example.org/other.js"></script>\n'
)


def test_only_plotly_cdn_scripts_are_found_once() -> None:
    assert find_cdn_scripts(HTML) == ["https://cdn.plot.ly/plotly-2.35.2.min.js"]
    assert find_cdn_scripts("<p>nothing</p>") == []


def test_scripts_are_downloaded_once_and_the_html_points_at_the_local_copy(tmp_path: Path) -> None:
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return b"/* plotly */"

    cache = PlotlyCache(tmp_path, fetch)

    first, changed = cache.localise(HTML)
    second, _ = cache.localise(HTML)

    assert changed
    assert fetched == ["https://cdn.plot.ly/plotly-2.35.2.min.js"]  # once, though used twice
    local = cache.path_for("https://cdn.plot.ly/plotly-2.35.2.min.js")
    assert local.read_bytes() == b"/* plotly */"
    assert local.as_uri() in first
    assert "cdn.plot.ly" not in first
    assert "https://example.org/other.js" in first  # other scripts are not touched
    assert first == second
    assert not list(tmp_path.glob("*.part"))


def test_offline_leaves_the_html_pointing_at_the_cdn(tmp_path: Path) -> None:
    def offline(url: str) -> bytes:
        raise OSError("no network")

    cache = PlotlyCache(tmp_path, offline)

    html, changed = cache.localise(HTML)

    assert not changed
    assert html == HTML
    assert cache.missing(HTML) == ["https://cdn.plot.ly/plotly-2.35.2.min.js"]
    assert cache.ensure("https://cdn.plot.ly/plotly-2.35.2.min.js") is None


def test_localising_without_downloading_uses_only_what_is_cached(tmp_path: Path) -> None:
    calls: list[str] = []
    cache = PlotlyCache(tmp_path, lambda url: calls.append(url) or b"js")  # type: ignore[func-returns-value]

    assert cache.localise(HTML, download=False) == (HTML, False)
    assert calls == []
    cache.ensure("https://cdn.plot.ly/plotly-2.35.2.min.js")
    assert cache.localise(HTML, download=False)[1]
    assert cache.missing(HTML) == []


def test_only_https_is_ever_fetched() -> None:
    with pytest.raises(ValueError, match="non-HTTPS"):
        download("http://cdn.plot.ly/plotly.js")
    with pytest.raises(ValueError, match="non-HTTPS"):
        download("file:///etc/passwd")
