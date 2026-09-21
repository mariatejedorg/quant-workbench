"""The libcst config editor: what it lists, what it rewrites, and what it refuses.

The central promise is *format preservation*: an edit changes the value it was asked to
change and nothing else. Most tests below therefore compare whole files, on the real
configuration files of the ten portfolio projects (``tests/fixtures/real_configs``) as well
as on small synthetic sources that isolate one behaviour.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import libcst as cst
import pytest

from quant_workbench.domain.config import Constant
from quant_workbench.domain.errors import UnsafeEditError
from quant_workbench.domain.literals import ValueKind, same_literal
from quant_workbench.infrastructure import config_editor
from quant_workbench.infrastructure.config_editor import LibCstConfigEditor

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "real_configs"
REAL_FILES = sorted(FIXTURES.glob("*.py"))

editor = LibCstConfigEditor()


def by_name(source: str) -> dict[str, Constant]:
    return {c.name: c for c in editor.constants(source)}


def changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    """Pairs of (old, new) for every line that differs; both must have the same line count."""
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    assert len(old) == len(new), "an edit of a one-line value must not add or remove lines"
    return [(a, b) for a, b in zip(old, new, strict=True) if a != b]


SAMPLE = dedent(
    '''\
    """Module docstring."""

    # --- Section heading, separated from what follows ---

    # The ticker to analyse.
    # Keep it to one symbol.
    TICKER = "AAPL"

    WINDOW = 252  # trading sessions in a year
    CAPITAL = 10_000.0
    TOLERANCE = 1e-6
    FLAG: bool = True
    NOTHING = None
    RANGE = (0.7, 1.3)
    NAMES = ["a", "b"]
    COMPUTED = 60 * 60
    ENV = os.environ.get("HOME")
    '''
)


# ----------------------------------------------------------------------- listing
def test_only_plain_literal_constants_are_listed() -> None:
    constants = by_name(SAMPLE)

    assert list(constants) == [
        "TICKER",
        "WINDOW",
        "CAPITAL",
        "TOLERANCE",
        "FLAG",
        "NOTHING",
        "RANGE",
        "NAMES",
    ]
    assert constants["TICKER"].kind is ValueKind.STR
    assert constants["WINDOW"].kind is ValueKind.INT
    assert constants["CAPITAL"].kind is ValueKind.FLOAT
    assert constants["FLAG"].kind is ValueKind.BOOL
    assert constants["NOTHING"].kind is ValueKind.NONE
    assert constants["RANGE"].kind is ValueKind.TUPLE
    assert constants["NAMES"].kind is ValueKind.LIST


def test_the_source_text_and_position_are_reported_as_written() -> None:
    capital = by_name(SAMPLE)["CAPITAL"]

    assert capital.source == "10_000.0"
    assert capital.value == 10_000.0
    assert SAMPLE.splitlines()[capital.line - 1] == "CAPITAL = 10_000.0"
    assert not capital.is_multiline


def test_descriptions_come_from_the_comments_next_to_the_constant() -> None:
    constants = by_name(SAMPLE)

    assert constants["TICKER"].description == "The ticker to analyse. Keep it to one symbol."
    assert constants["WINDOW"].description == "trading sessions in a year"
    assert constants["CAPITAL"].description == ""


def test_a_blank_line_stops_the_comment_block() -> None:
    # "--- Section heading ---" is separated from TICKER by a blank line: not its description
    assert "Section" not in by_name(SAMPLE)["TICKER"].description


def test_a_header_comment_at_the_top_of_a_file_describes_the_first_constant() -> None:
    constants = by_name("# Ticker analysed.\nTICKER = 'A'\n")

    assert constants["TICKER"].description == "Ticker analysed."


def test_symbols_restrict_the_listing() -> None:
    assert list(editor.constants(SAMPLE, symbols=["WINDOW", "NAMES"]) and by_name(SAMPLE)) != []
    restricted = editor.constants(SAMPLE, symbols=["WINDOW", "NAMES"])

    assert [c.name for c in restricted] == ["WINDOW", "NAMES"]


def test_multiline_constants_report_their_span() -> None:
    source = 'X = {\n    "a": 1,\n    "b": 2,\n}\nY = 1\n'
    x = by_name(source)["X"]

    assert (x.line, x.end_line) == (1, 4)
    assert x.is_multiline
    assert by_name(source)["Y"].line == 5


def test_constants_that_are_not_module_level_statements_are_ignored() -> None:
    source = dedent(
        """\
        A = 1; B = 2
        C = D = 3
        E, F = 1, 2
        obj.attr = 5
        def f():
            INNER = 1
        class K:
            FIELD = 2
        if True:
            COND = 3
        G = 4
        """
    )

    assert list(by_name(source)) == ["G"]


def test_a_constant_assigned_twice_is_ambiguous_and_not_listed() -> None:
    assert list(by_name("A = 1\nA = 2\nB = 3\n")) == ["B"]


def test_annotated_assignments_are_supported() -> None:
    assert by_name("X: int = 5\nY: int\n")["X"].value == 5


def test_a_syntax_error_is_reported_as_a_workbench_error() -> None:
    with pytest.raises(UnsafeEditError, match="syntax error"):
        editor.constants("X = (\n")


# ------------------------------------------------------------------ scalar edits
def test_only_the_edited_line_changes() -> None:
    after = editor.edit(SAMPLE, {"WINDOW": 126})

    assert changed_lines(SAMPLE, after) == [
        (
            "WINDOW = 252  # trading sessions in a year\n",
            "WINDOW = 126  # trading sessions in a year\n",
        )
    ]


def test_several_constants_can_be_edited_at_once() -> None:
    after = editor.edit(SAMPLE, {"TICKER": "MSFT", "FLAG": False, "NOTHING": "x"})

    constants = by_name(after)
    assert constants["TICKER"].value == "MSFT"
    assert constants["FLAG"].value is False
    assert constants["NOTHING"].value == "x"
    assert len(changed_lines(SAMPLE, after)) == 3


def test_the_writing_style_of_the_old_value_is_kept() -> None:
    source = "A = 'single'\nB = 10_000\nC = 2.5e-3\nD = (0.5)\n"

    after = editor.edit(source, {"A": "it's", "B": 2_500_000, "C": 0.001, "D": 0.75})

    assert after == "A = 'it\\'s'\nB = 2_500_000\nC = 0.001\nD = (0.75)\n"


def test_setting_a_value_to_itself_does_not_touch_the_file() -> None:
    source = "A = 0.90\nB = 10_000.0\nC = 'x'\n"

    assert editor.edit(source, {"A": 0.9, "B": 10000.0, "C": "x"}) == source
    assert editor.edit(source, {}) == source


def test_an_int_is_accepted_for_a_float_constant() -> None:
    assert editor.edit("R = 0.5\n", {"R": 2}) == "R = 2.0\n"


def test_negative_numbers_and_annotations() -> None:
    assert editor.edit("X: float = 1.5\n", {"X": -0.25}) == "X: float = -0.25\n"


def test_accented_and_special_text_survives() -> None:
    after = editor.edit('NAME = "x"\n', {"NAME": 'Cartera "Europa" — María\n2'})

    assert by_name(after)["NAME"].value == 'Cartera "Europa" — María\n2'
    assert after.count("\n") == 1  # the newline inside the value is escaped, not written


def test_windows_line_endings_are_preserved() -> None:
    source = SAMPLE.replace("\n", "\r\n")

    after = editor.edit(source, {"WINDOW": 5, "RANGE": (0.5, 1.5, 2.5), "NAMES": ["a", "b", "c"]})

    assert after.count("\r\n") == after.count("\n")
    assert after.replace("\r\n", "\n").count("\r") == 0


# ----------------------------------------------------------------- refusals
def test_type_changes_are_refused() -> None:
    with pytest.raises(UnsafeEditError, match="WINDOW is a int; got str"):
        editor.edit(SAMPLE, {"WINDOW": "many"})
    with pytest.raises(UnsafeEditError, match="got bool"):
        editor.edit(SAMPLE, {"WINDOW": True})


def test_unknown_names_suggest_the_closest_ones() -> None:
    with pytest.raises(UnsafeEditError, match="No editable constant named TICKR") as raised:
        editor.edit(SAMPLE, {"TICKR": "x"})

    assert raised.value.hint is not None
    assert "TICKER" in raised.value.hint


def test_computed_constants_are_refused_with_a_reason() -> None:
    with pytest.raises(UnsafeEditError, match="computed"):
        editor.edit(SAMPLE, {"COMPUTED": 5})


def test_ambiguous_constants_are_refused_with_a_reason() -> None:
    with pytest.raises(UnsafeEditError, match="more than once"):
        editor.edit("A = 1\nA = 2\n", {"A": 3})


def test_a_failing_edit_leaves_no_partial_result() -> None:
    with pytest.raises(UnsafeEditError):
        editor.edit(SAMPLE, {"WINDOW": 5, "TICKER": 3})  # the second one is invalid


def test_values_without_a_literal_form_are_refused() -> None:
    with pytest.raises(UnsafeEditError, match="cannot be written"):
        editor.edit(SAMPLE, {"CAPITAL": float("inf")})


# ---------------------------------------------------------------- container edits
DICT_SOURCE = dedent(
    """\
    TICKERS = {
        "^IBEX": "IBEX 35",  # index
        "SAN.MC": "Santander",
        # a comment between entries
        "ITX.MC": "Inditex",
    }
    AFTER = 1
    """
)


def test_untouched_dict_entries_keep_their_comments_and_layout() -> None:
    new = {"^IBEX": "IBEX 35", "SAN.MC": "Banco Santander", "ITX.MC": "Inditex"}

    after = editor.edit(DICT_SOURCE, {"TICKERS": new})

    assert after == DICT_SOURCE.replace('"Santander"', '"Banco Santander"')


def test_adding_an_entry_appends_a_line_in_the_same_style() -> None:
    old = by_name(DICT_SOURCE)["TICKERS"].value
    assert isinstance(old, dict)

    after = editor.edit(DICT_SOURCE, {"TICKERS": {**old, "REP.MC": "Repsol"}})

    assert after == DICT_SOURCE.replace(
        '    "ITX.MC": "Inditex",\n', '    "ITX.MC": "Inditex",\n    "REP.MC": "Repsol",\n'
    )


def test_removing_the_first_middle_and_last_entries() -> None:
    old = by_name(DICT_SOURCE)["TICKERS"].value
    assert isinstance(old, dict)

    without_first = editor.edit(
        DICT_SOURCE, {"TICKERS": {k: v for k, v in old.items() if k != "^IBEX"}}
    )
    assert '"^IBEX"' not in without_first
    assert "# index" not in without_first  # the comment went with its entry
    assert "# a comment between entries" in without_first

    without_last = editor.edit(
        DICT_SOURCE, {"TICKERS": {k: v for k, v in old.items() if k != "ITX.MC"}}
    )
    # the own-line comment introduced ITX.MC, so it leaves together with it
    assert without_last == (
        'TICKERS = {\n    "^IBEX": "IBEX 35",  # index\n    "SAN.MC": "Santander",\n}\nAFTER = 1\n'
    )


def test_a_comment_is_never_copied_onto_a_new_entry() -> None:
    old = by_name(DICT_SOURCE)["TICKERS"].value
    assert isinstance(old, dict)

    after = editor.edit(DICT_SOURCE, {"TICKERS": {"NEW": "n", **old}})  # inserted before "^IBEX"

    assert after.count("# index") == 1


def test_reordering_entries() -> None:
    after = editor.edit("D = {'a': 1, 'b': 2, 'c': 3}\n", {"D": {"c": 3, "a": 1, "b": 2}})

    assert after == "D = {'c': 3, 'a': 1, 'b': 2}\n"


def test_single_line_containers_stay_single_line() -> None:
    assert editor.edit("D = {'a': 1, 'b': 2}\n", {"D": {"a": 1, "b": 2, "c": 3}}) == (
        "D = {'a': 1, 'b': 2, 'c': 3}\n"
    )
    assert editor.edit("L = [1, 2, 3]\n", {"L": [1, 2]}) == "L = [1, 2]\n"
    assert editor.edit("L = [1, 2]\n", {"L": [1, 2, 3, 4]}) == "L = [1, 2, 3, 4]\n"
    assert editor.edit("L = [1, 2, 3]\n", {"L": [1, 9, 3]}) == "L = [1, 9, 3]\n"


def test_trailing_commas_are_respected() -> None:
    assert editor.edit("L = [1, 2,]\n", {"L": [1, 2, 3]}) == "L = [1, 2, 3,]\n"


def test_tuples_including_the_one_element_case() -> None:
    assert editor.edit("T = (0.7, 1.3)\n", {"T": (0.5, 1.5)}) == "T = (0.5, 1.5)\n"
    assert editor.edit("T = (1,)\n", {"T": (1, 2)}) == "T = (1, 2)\n"
    assert editor.edit("T = (1, 2)\n", {"T": (7,)}) == "T = (7,)\n"
    assert editor.edit("T = 1, 2\n", {"T": (3, 4)}) == "T = 3, 4\n"


def test_a_list_is_accepted_for_a_tuple_constant() -> None:
    assert editor.edit("T = (1, 2)\n", {"T": [3, 4]}) == "T = (3, 4)\n"


def test_nested_values_are_merged_recursively() -> None:
    source = dedent(
        """\
        H = {
            "A": {"peso": 0.5, "nombre": "a"},  # first
            "B": {"peso": 0.5, "nombre": "b"},
        }
        """
    )

    after = editor.edit(
        source, {"H": {"A": {"peso": 0.6, "nombre": "a"}, "B": {"peso": 0.5, "nombre": "b"}}}
    )

    assert after == source.replace('{"peso": 0.5, "nombre": "a"}', '{"peso": 0.6, "nombre": "a"}')


def test_emptying_and_refilling_containers() -> None:
    assert editor.edit("L = [1, 2]\n", {"L": []}) == "L = []\n"
    assert editor.edit("L = []\n", {"L": [1, 2]}) == "L = [1, 2]\n"
    assert editor.edit("D = {}\n", {"D": {"a": 1}}) == 'D = {"a": 1}\n'


def test_windows_line_endings_survive_a_multiline_dict_edit() -> None:
    source = DICT_SOURCE.replace("\n", "\r\n")
    old = by_name(source)["TICKERS"].value
    assert isinstance(old, dict)

    after = editor.edit(source, {"TICKERS": {**old, "REP.MC": "Repsol"}})

    assert after.count("\r\n") == after.count("\n")
    assert after == source.replace('"Inditex",\r\n', '"Inditex",\r\n    "REP.MC": "Repsol",\r\n')


# ---------------------------------------------------------- the ten real files
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("path", REAL_FILES, ids=lambda p: p.name)
def test_real_files_survive_an_edit_of_every_scalar(path: Path, newline: str) -> None:
    source = path.read_text(encoding="utf-8").replace("\n", newline)
    constants = editor.constants(source)
    assert constants, "every real file has editable constants"

    for constant in constants:
        replacement = _other_scalar(constant)
        if replacement is None:
            continue
        after = editor.edit(source, {constant.name: replacement})

        changed = changed_lines(source, after)
        assert len(changed) == 1, f"{constant.name}: exactly one line differs"
        assert same_literal(by_name(after)[constant.name].value, replacement)
        assert {n: c.value for n, c in by_name(after).items() if n != constant.name} == {
            n: c.value for n, c in by_name(source).items() if n != constant.name
        }
        assert after.count("\r\n") == (after.count("\n") if newline == "\r\n" else 0)


@pytest.mark.parametrize("path", REAL_FILES, ids=lambda p: p.name)
def test_real_files_are_byte_identical_when_nothing_changes(path: Path) -> None:
    source = path.read_text(encoding="utf-8").replace("\n", "\r\n")
    everything = {c.name: c.value for c in editor.constants(source)}

    assert editor.edit(source, everything) == source


def test_the_dict_configs_of_the_real_projects_can_grow_and_shrink() -> None:
    source = (FIXTURES / "10_multi_strategy.py").read_text(encoding="utf-8")
    tickers = by_name(source)["TICKERS"].value
    assert isinstance(tickers, dict)
    grown = {**tickers, "TEF.MC": "Telefónica"}

    after = editor.edit(source, {"TICKERS": grown})

    assert changed_lines_allowing_growth(source, after) == ['    "TEF.MC": "Telefónica",\n']
    assert "TEF.MC" not in editor.edit(after, {"TICKERS": tickers})
    assert editor.edit(after, {"TICKERS": tickers}) == source  # and back, byte for byte


def changed_lines_allowing_growth(before: str, after: str) -> list[str]:
    """The lines present only in ``after`` (the file must otherwise be untouched)."""
    old = before.splitlines(keepends=True)
    added = [line for line in after.splitlines(keepends=True) if line not in old]
    assert [line for line in after.splitlines(keepends=True) if line in old] == old
    return added


def _other_scalar(constant: Constant) -> object | None:
    """A different value of the same type, or ``None`` for containers."""
    value = constant.value
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 7
    if isinstance(value, float):
        return value * 2 + 0.125
    if isinstance(value, str):
        return value + "-x"
    return None


# ------------------------------------------------------- edge cases and the safety net
def test_values_that_are_literals_but_not_editable_ones_are_not_listed() -> None:
    assert by_name("S = {1, 2}\nC = 1j\nB = b'x'\nOK = 1\n").keys() == {"OK"}


def test_one_line_suites_are_not_module_level_and_are_left_alone() -> None:
    source = "if True: X = 1\nY = 2\n"

    assert by_name(source).keys() == {"Y"}
    assert editor.edit(source, {"Y": 3}) == "if True: X = 1\nY = 3\n"


def test_a_dict_with_duplicate_keys_is_replaced_as_a_whole() -> None:
    after = editor.edit("D = {'a': 1, 'a': 2}\n", {"D": {"a": 3, "b": 4}})

    assert after == "D = {'a': 3, 'b': 4}\n"  # the quote style of the original is kept


def test_the_read_back_check_refuses_an_edit_that_went_wrong(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the rewrite ever produced the wrong value, the file must not be returned."""

    def wrong(self: object, old: object, new: object) -> cst.BaseExpression:
        return cst.parse_expression("999")

    monkeypatch.setattr(config_editor._Values, "_replace", wrong)

    with pytest.raises(UnsafeEditError, match="would not read back as requested"):
        editor.edit("WINDOW = 5\n", {"WINDOW": 6})
