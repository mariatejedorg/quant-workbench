"""The Qt-free parts of the UI package: ANSI parsing, the command registry, theme, summary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from quant_workbench.application.catalog import Catalog
from quant_workbench.application.settings import Settings, load_settings, save_settings
from quant_workbench.domain.diagnostics import ExplainabilityScore, Finding, Severity
from quant_workbench.domain.graph import Dependency, DependencyGraph, EdgeOrigin
from quant_workbench.domain.runs import JobKind, MetricValue, Run, RunStatus
from quant_workbench.ui.ansi import AnsiParser, AnsiStyle
from quant_workbench.ui.commands import Command, CommandRegistry, fuzzy_score
from quant_workbench.ui.summary import format_duration, format_metric, project_summary_html
from quant_workbench.ui.theme import (
    DARK,
    LIGHT,
    build_stylesheet,
    contrast_ratio,
    severity_colour,
    status_colour,
    tokens_for,
)
from tests.fakes import make_project

# ------------------------------------------------------------------------- ANSI


def runs(parser: AnsiParser, text: str) -> list[tuple[str, AnsiStyle]]:
    return parser.feed(text)


def test_plain_text_is_one_run_with_the_default_style() -> None:
    assert runs(AnsiParser(), "hello") == [("hello", AnsiStyle())]
    assert runs(AnsiParser(), "") == []


def test_colours_bold_and_reset() -> None:
    parser = AnsiParser()

    out = runs(parser, "a\x1b[1;31mred bold\x1b[0m b")

    assert [text for text, _ in out] == ["a", "red bold", " b"]
    assert out[1][1].bold
    assert out[1][1].fg == "#cd3131"
    assert out[2][1] == AnsiStyle()


def test_bright_background_and_underline_codes() -> None:
    (only,) = runs(AnsiParser(), "\x1b[4;92;45mx")

    assert only[1].underline
    assert only[1].fg == "#23d18b"  # bright green
    assert only[1].bg == "#bc3fbc"


def test_256_and_true_colour() -> None:
    parser = AnsiParser()

    (basic,) = runs(parser, "\x1b[38;5;1mx")
    (cube,) = runs(parser, "\x1b[38;5;196my")
    (grey,) = runs(parser, "\x1b[38;5;240mz")
    (true,) = runs(parser, "\x1b[38;2;10;20;30;48;2;255;0;0mw")

    assert basic[1].fg == "#cd3131"
    assert cube[1].fg == "#ff0000"
    assert grey[1].fg == "#585858"
    assert true[1].fg == "#0a141e"
    assert true[1].bg == "#ff0000"


def test_the_style_persists_across_lines_until_reset() -> None:
    parser = AnsiParser()
    runs(parser, "\x1b[32mgreen")

    (second,) = runs(parser, "still green")
    parser.reset()
    (third,) = runs(parser, "plain")

    assert second[1].fg == "#0dbc79"
    assert third[1] == AnsiStyle()


def test_non_colour_sequences_disappear() -> None:
    out = runs(AnsiParser(), "a\x1b[2Kb\x1b[1;1Hc\x1b[?25ld")

    assert "".join(text for text, _ in out) == "abcd"


def test_a_sequence_split_between_chunks_is_completed() -> None:
    parser = AnsiParser()

    first = runs(parser, "one \x1b[3")
    second = runs(parser, "1mtwo")

    assert first == [("one ", AnsiStyle())]
    assert second == [("two", AnsiStyle(fg="#cd3131"))]


def test_a_lone_escape_at_the_end_is_held_back() -> None:
    parser = AnsiParser()

    assert runs(parser, "x\x1b") == [("x", AnsiStyle())]
    assert runs(parser, "[0mdone") == [("done", AnsiStyle())]


def test_removing_bold_and_default_colours() -> None:
    parser = AnsiParser()
    runs(parser, "\x1b[1;2;31;42m")

    (a,) = runs(parser, "a")
    parser.feed("\x1b[22;39;49m")
    (b,) = runs(parser, "b")

    assert (a[1].bold, a[1].dim) == (True, True)
    assert b[1] == AnsiStyle()


def test_incomplete_extended_colours_are_ignored() -> None:
    (out,) = runs(AnsiParser(), "\x1b[38;5mx")

    assert out[1].fg is None


# --------------------------------------------------------------- command registry


def test_fuzzy_score_needs_every_letter_in_order() -> None:
    assert fuzzy_score("rnl", "Run all") is not None
    assert fuzzy_score("zzz", "Run all") is None
    assert fuzzy_score("ra", "Run all") is not None
    assert fuzzy_score("ar", "Run all") is None  # no "r" after the first "a"
    assert fuzzy_score("", "anything") == 0


def test_fuzzy_score_prefers_word_starts_and_consecutive_letters() -> None:
    assert fuzzy_score("run", "Run all") > fuzzy_score("run", "Turn around")  # type: ignore[operator]
    assert fuzzy_score("ra", "Run all") > fuzzy_score("ra", "Reload projects")  # type: ignore[operator]
    assert fuzzy_score("RUN", "run") == fuzzy_score("run", "RUN")  # case-insensitive


def make_registry(calls: list[str]) -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(Command("a", "Run all projects", lambda: calls.append("a"), "Ctrl+R", "Run"))
    registry.register(Command("b", "Run selected", lambda: calls.append("b"), category="Run"))
    registry.register(
        Command(
            "c", "Diagnose", lambda: calls.append("c"), enabled=lambda: False, category="Doctor"
        )
    )
    return registry


def test_search_ranks_matches_and_skips_disabled_commands() -> None:
    registry = make_registry([])

    assert [c.id for c in registry.search("run")] == ["a", "b"]
    assert [c.id for c in registry.search("all")] == ["a"]
    assert registry.search("diagnose") == []  # disabled
    assert [c.id for c in registry.search("")] == ["a", "b"]  # the disabled one is hidden
    assert registry.search("zzz") == []


def test_search_limits_the_results() -> None:
    registry = CommandRegistry()
    for number in range(30):
        registry.register(Command(f"c{number}", f"Command {number}", lambda: None))

    assert len(registry.search("command", limit=5)) == 5


def test_execute_runs_only_enabled_commands() -> None:
    calls: list[str] = []
    registry = make_registry(calls)

    assert registry.execute("a")
    assert not registry.execute("c")
    assert calls == ["a"]


def test_duplicate_ids_are_rejected_and_labels_include_the_category() -> None:
    registry = make_registry([])

    with pytest.raises(ValueError, match="already registered"):
        registry.register(Command("a", "again", lambda: None))
    assert registry.get("a").label == "Run: Run all projects"
    assert registry.get("c").label == "Doctor: Diagnose"
    assert len(registry) == 3
    assert [c.id for c in registry.all()] == ["a", "b", "c"]


# ------------------------------------------------------------------------ theme


@pytest.mark.parametrize("tokens", [LIGHT, DARK], ids=lambda t: t.name)
def test_text_meets_wcag_aa_contrast_on_its_backgrounds(tokens) -> None:  # type: ignore[no-untyped-def]
    for foreground in (tokens.ink, tokens.ink_secondary, tokens.ink_muted):
        assert contrast_ratio(foreground, tokens.surface) >= 4.5, foreground
        assert contrast_ratio(foreground, tokens.window) >= 4.5, foreground
    assert contrast_ratio(tokens.console_fg, tokens.console_bg) >= 7
    for colour in (tokens.error, tokens.warning, tokens.success, tokens.accent):
        assert contrast_ratio(colour, tokens.surface) >= 3, colour  # large text / badges


def test_contrast_ratio_extremes() -> None:
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21)
    assert contrast_ratio("#777777", "#777777") == pytest.approx(1)


def test_theme_selection_and_stylesheet() -> None:
    assert tokens_for("light") is LIGHT
    assert tokens_for("dark") is DARK
    assert tokens_for("system", system_is_dark=True) is DARK
    assert tokens_for("system", system_is_dark=False) is LIGHT
    css = build_stylesheet(DARK)
    assert DARK.window in css
    assert DARK.accent in css
    assert "{" in css
    assert "{{" not in css  # the f-string escapes are resolved


def test_status_and_severity_colours() -> None:
    assert status_colour(None, LIGHT) == LIGHT.ink_muted
    assert status_colour(RunStatus.SUCCEEDED, LIGHT) == LIGHT.success
    assert status_colour(RunStatus.FAILED, LIGHT) == LIGHT.error
    assert status_colour(RunStatus.TIMED_OUT, LIGHT) == LIGHT.error
    assert status_colour(RunStatus.RUNNING, LIGHT) == LIGHT.accent
    assert status_colour(RunStatus.CANCELLED, LIGHT) == LIGHT.warning
    assert severity_colour(Severity.ERROR, DARK) == DARK.error
    assert severity_colour(Severity.INFO, DARK) == DARK.ink_muted


# ---------------------------------------------------------------------- summary
T0 = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def catalog_of(*slugs: str, edges: list[Dependency] | None = None) -> Catalog:
    projects = tuple(make_project(Path("ws"), s) for s in slugs)
    return Catalog(Path("ws"), projects, DependencyGraph([p.slug for p in projects], edges or []))


def test_durations_and_metrics_are_formatted_for_people() -> None:
    assert format_duration(None) == "-"
    assert format_duration(timedelta(seconds=12.34)) == "12.3 s"
    assert format_duration(timedelta(seconds=125)) == "2 min 05 s"
    assert format_metric(MetricValue("sharpe", 1.23456)) == "1.235"
    assert format_metric(MetricValue("n", 10_000)) == "10000"
    assert format_metric(MetricValue("cap", 5.4e10, "USD")) == "5.4e+10 USD"


def test_the_summary_of_a_project_that_never_ran() -> None:
    catalog = catalog_of("alpha", "beta")
    project = catalog.projects[0]

    html = project_summary_html(
        project, catalog, last_run=None, score=None, findings=(), tokens=LIGHT
    )

    assert "Alpha" in html
    assert "has not been run yet" in html
    assert "Run the doctor" in html


def test_the_summary_reports_the_last_run_metrics_and_health() -> None:
    edges = [Dependency("beta", "alpha", frozenset({EdgeOrigin.DECLARED}))]
    catalog = catalog_of("alpha", "beta", edges=edges)
    alpha = catalog.projects[0]
    run = Run(
        id="r1",  # type: ignore[arg-type]
        project=alpha.slug,
        kind=JobKind.RUN,
        status=RunStatus.FAILED,
        queued_at=T0,
        started_at=T0,
        finished_at=T0 + timedelta(seconds=5),
        attempt=2,
        failure="ValueError: <boom>",
        metrics=(MetricValue("sharpe", 1.5),),
        peak_rss_bytes=200 * 1024 * 1024,
        avg_cpu_percent=42.0,
    )
    score = ExplainabilityScore(88.0, 0.9, 0.2, 1.0, 1.0, 12, 300)
    findings = (
        Finding("c", "x", Severity.WARNING, "w", project=alpha.slug),
        Finding("c", "y", Severity.INFO, "i", project=alpha.slug),
    )

    html = project_summary_html(
        alpha, catalog, last_run=run, score=score, findings=findings, tokens=LIGHT
    )

    assert "FAILED" in html
    assert "attempt 2" in html
    assert "ValueError: &lt;boom&gt;" in html  # escaped: a failure message is not HTML
    assert "sharpe" in html
    assert "200 MB" in html
    assert "88/100" in html
    assert "1 warning" in html
    assert "1 info" in html
    assert "beta" in html  # used by


# --------------------------------------------------------------- settings file


def test_settings_round_trip_through_the_toml_file(tmp_path: Path) -> None:
    settings = Settings(
        workspace_root=tmp_path / "Quant - María",
        max_concurrency=5,
        expected_git_email="m@example.org",
        expected_git_name='Ma "ría"',
        disabled_checkers=("determinism", "outputs"),
        theme="dark",
        commit_trailer="",
    )
    target = tmp_path / "config" / "settings.toml"

    save_settings(settings, target)
    loaded = load_settings(target)

    assert loaded.model_dump() == settings.model_dump()
    assert not list(target.parent.glob("*.tmp"))


def test_unset_values_survive_the_round_trip_as_none(tmp_path: Path) -> None:
    target = tmp_path / "settings.toml"

    save_settings(Settings(expected_git_remote_host=None), target)

    # the default of this field is not None: writing nothing would silently bring it back
    assert load_settings(target).expected_git_remote_host is None
    assert load_settings(target).workspace_root is None
