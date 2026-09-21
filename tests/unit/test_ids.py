from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from quant_workbench.domain.errors import InvalidSlugError, WorkbenchError
from quant_workbench.domain.ids import new_run_id, parse_slug
from tests.conftest import FakeClock


@pytest.mark.parametrize(
    "value",
    ["credit-risk-merton-model", "a", "project-10", "garch-volatility-forecasting", "x1-y2"],
)
def test_valid_slugs_are_accepted(value: str) -> None:
    assert parse_slug(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "Has-Capitals",
        "under_score",
        "-leading",
        "trailing-",
        "double--hyphen",
        "sp ace",
        "ñandú",
        "a" * 65,
    ],
)
def test_invalid_slugs_are_rejected_with_a_hint(value: str) -> None:
    with pytest.raises(InvalidSlugError) as excinfo:
        parse_slug(value)
    assert excinfo.value.hint is not None
    assert isinstance(excinfo.value, WorkbenchError)


@given(st.from_regex(r"[a-z0-9]+(-[a-z0-9]+)*", fullmatch=True).filter(lambda s: len(s) <= 64))
def test_any_kebab_case_string_round_trips(value: str) -> None:
    assert parse_slug(value) == value


def test_run_ids_sort_chronologically(clock: FakeClock) -> None:
    first = new_run_id(clock)
    clock.advance(milliseconds=5)
    second = new_run_id(clock)
    clock.advance(seconds=3600)
    third = new_run_id(clock)
    assert first < second < third


def test_run_ids_are_unique_even_within_the_same_millisecond(clock: FakeClock) -> None:
    ids = {new_run_id(clock) for _ in range(500)}
    assert len(ids) == 500
