from __future__ import annotations

from textwrap import dedent

import pytest

from quant_workbench.domain.code_analysis import (
    COMPLEXITY_LIMIT,
    cyclomatic_complexity,
    explainability,
    find_secrets,
    imports_module,
    lint,
    measure,
    parse,
    string_literals,
    uncleaned_foreign_path_inserts,
)
from quant_workbench.domain.git import GitState, remote_host


def tree(source: str):  # type: ignore[no-untyped-def]
    parsed = parse(dedent(source))
    assert parsed is not None
    return parsed


# ----------------------------------------------------------------------- parsing
def test_parse_returns_none_for_broken_source() -> None:
    assert parse("def f(:\n") is None
    assert parse("x = 1\n") is not None
    assert parse("\x00") is None  # null bytes make ast.parse raise ValueError


def test_string_literals_and_imports() -> None:
    parsed = tree(
        """
        import yfinance as yf
        from pandas.io import formats
        NAME = "proyecto-6"
        """
    )

    assert ("proyecto-6", 4) in list(string_literals(parsed))
    assert imports_module(parsed, "yfinance")
    assert imports_module(parsed, "pandas")  # a submodule import counts
    assert imports_module(parsed, "pandas.io")
    assert not imports_module(parsed, "numpy")
    assert not imports_module(tree("import yfinance_fake\n"), "yfinance")


# ------------------------------------------------------------------- complexity
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("return 1", 1),
        ("if a:\n        return 1\n    return 2", 2),
        ("for i in x:\n        if i:\n            pass", 3),
        ("return a and b and c", 3),
        ("return [i for i in x if i if i > 1]", 4),
        ("try:\n        pass\n    except A:\n        pass\n    except B:\n        pass", 3),
        ("while a:\n        pass", 2),
        ("return 1 if a else 2", 2),
    ],
)
def test_cyclomatic_complexity(body: str, expected: int) -> None:
    parsed = tree(f"def f(a, b, c, x):\n    {body}\n")

    assert cyclomatic_complexity(parsed) == expected


# ---------------------------------------------------------------- explainability
WELL_EXPLAINED = '''\
"""Prices."""


def average(values):
    """Mean of the values."""
    # empty input has no mean, so return 0 instead of dividing by zero
    if not values:
        return 0.0
    return sum(values) / len(values)
'''

UNEXPLAINED = """\
def f(a):
    if a:
        return 1
    return 2


def g(b):
    return b
"""


def test_a_documented_file_scores_higher_than_an_undocumented_one() -> None:
    good = measure(WELL_EXPLAINED)
    bad = measure(UNEXPLAINED)
    assert good is not None
    assert bad is not None

    assert explainability([good]).score > 85
    assert explainability([bad]).score < 60
    assert explainability([good]).docstring_coverage == 1.0
    assert explainability([bad]).docstring_coverage == 0.0


def test_docstrings_and_comments_count_as_explanation_not_as_code() -> None:
    metrics = measure(WELL_EXPLAINED)

    assert metrics is not None
    assert metrics.comment_lines == 3  # module docstring, function docstring, one comment
    assert metrics.code_lines == 4  # the def line, the `if`, and the two returns


def test_private_helpers_do_not_need_a_docstring() -> None:
    metrics = measure('"""M."""\n\n\ndef _helper():\n    return 1\n')

    assert metrics is not None
    assert (metrics.documentable, metrics.documented) == (1, 1)


def test_methods_and_classes_are_documentable_but_nested_functions_are_not() -> None:
    source = '"""M."""\n\n\nclass A:\n    """A."""\n\n    def m(self):\n        def inner():\n            pass\n'
    metrics = measure(source)

    assert metrics is not None
    assert metrics.documentable == 3  # module, A, A.m  (inner is not counted)
    assert metrics.documented == 2


def test_long_or_complex_functions_lower_the_score() -> None:
    long_body = "\n".join(f"    x{i} = {i}" for i in range(60))
    complex_body = "\n".join(f"    if a{i}:\n        pass" for i in range(COMPLEXITY_LIMIT + 2))
    metrics = measure(
        f'"""M."""\n\n\ndef long():\n    """L."""\n{long_body}\n\n\n'
        f'def branchy(a0):\n    """B."""\n{complex_body}\n'
    )

    assert metrics is not None
    score = explainability([metrics])
    assert score.short_function_share == 0.5
    assert score.simple_function_share == 0.5


def test_a_file_with_a_syntax_error_has_no_metrics() -> None:
    assert measure("def broken(:\n") is None


def test_no_functions_means_full_marks_for_shape() -> None:
    metrics = measure("X = 1\n")
    assert metrics is not None

    score = explainability([metrics])

    assert score.short_function_share == score.simple_function_share == 1.0


# -------------------------------------------------------------- import collisions
OWN_ROOT = "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n"


def test_inserting_the_projects_own_root_is_fine() -> None:
    assert uncleaned_foreign_path_inserts(tree(OWN_ROOT)) == ()


def test_own_root_spelled_with_dirname_or_parents_is_fine() -> None:
    dirname = "import os, sys\nsys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
    parents = (
        "import sys\nfrom pathlib import Path\nsys.path.append(str(Path(__file__).parents[1]))\n"
    )

    assert uncleaned_foreign_path_inserts(tree(dirname)) == ()
    assert uncleaned_foreign_path_inserts(tree(parents)) == ()


def test_inserting_a_sibling_project_without_cleanup_is_reported() -> None:
    source = """
    import sys
    from pathlib import Path

    def load(name):
        module_path = Path(__file__).resolve().parents[2] / name / "src"
        sys.path.insert(0, str(module_path))
        import config
        return config
    """

    (mutation,) = uncleaned_foreign_path_inserts(tree(source))

    assert mutation.scope == "load"
    assert mutation.line == 7


def test_a_literal_or_variable_path_is_foreign_too() -> None:
    assert (
        len(uncleaned_foreign_path_inserts(tree("import sys\nsys.path.append('../other')\n"))) == 1
    )
    assert len(uncleaned_foreign_path_inserts(tree("import sys\nsys.path.insert(0, where)\n"))) == 1


def test_a_loader_that_restores_sys_modules_is_accepted() -> None:
    source = """
    import sys

    def load(module_path):
        saved = {n: m for n, m in sys.modules.items() if n == "config"}
        sys.path.insert(0, str(module_path.parent))
        try:
            pass
        finally:
            sys.path.remove(str(module_path.parent))
            sys.modules.update(saved)
    """

    assert uncleaned_foreign_path_inserts(tree(source)) == ()


def test_cleanup_in_another_function_does_not_excuse_the_insertion() -> None:
    source = """
    import sys

    def clean():
        sys.modules.pop("config", None)

    def load(path):
        sys.path.insert(0, path)
    """

    (mutation,) = uncleaned_foreign_path_inserts(tree(source))

    assert mutation.scope == "load"


# ------------------------------------------------------------------------- lint
def test_lint_finds_bug_patterns() -> None:
    source = """
    import os
    import sys as system
    from typing import List

    __all__ = ["List"]

    def f(items=[], flag=False, *, cache={}):
        try:
            return 1
        except:
            pass

    def g():
        try:
            return 1
        except ValueError:
            pass
    """

    found = lint(tree(source))
    issues = {(i.code, i.line) for i in found}

    assert ("bare-except", 11) in issues
    assert ("silent-except", 17) in issues
    assert ("mutable-default", 8) in issues
    assert sum(1 for i in found if i.code == "mutable-default") == 2  # `items` and `cache`
    assert ("unused-import", 2) in issues  # os
    assert ("unused-import", 3) in issues  # system
    assert not any(code == "unused-import" and line == 4 for code, line in issues)  # in __all__


def test_used_imports_are_not_reported() -> None:
    source = "import os\nfrom pathlib import Path\nprint(os.name, Path)\nfrom __future__ import annotations\n"

    assert lint(tree(source)) == ()


def test_a_clean_file_has_no_lint_issues() -> None:
    assert lint(tree(WELL_EXPLAINED)) == ()


# ----------------------------------------------------------------------- secrets
@pytest.mark.parametrize(
    ("text", "kind", "certain"),
    [
        ("-----BEGIN RSA PRIVATE KEY-----", "private-key", True),
        ("-----BEGIN PRIVATE KEY-----", "private-key", True),
        ("key = 'AKIAABCDEFGHIJKLMNOP'", "aws-access-key", True),
        ("token: ghp_" + "a" * 36, "github-token", True),
        ("SLACK = 'xoxb-1234567890-abcdef'", "slack-token", True),
        ("API_KEY = 'abcdef0123456789abcdef'", "hardcoded-credential", False),
        ('password: "s3cr3t-value-that-is-long"', "hardcoded-credential", False),
    ],
)
def test_secrets_are_recognised(text: str, kind: str, certain: bool) -> None:
    (hit,) = find_secrets(f"line one\n{text}\n")

    assert (hit.kind, hit.line, hit.certain) == (kind, 2, certain)


@pytest.mark.parametrize(
    "text",
    [
        "-----BEGIN CERTIFICATE-----",  # public certificates (the CA bundle) are not secrets
        "API_KEY = os.environ['API_KEY']",
        "password = ''",
        "token = 'short'",
        "# the api key goes here",
    ],
)
def test_ordinary_text_is_not_flagged(text: str) -> None:
    assert find_secrets(text) == ()


# --------------------------------------------------------------------------- git
@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("git@github.com-maria:mariatejedorg/repo.git", "github.com-maria"),
        ("git@github.com:owner/repo.git", "github.com"),
        ("https://github.com/owner/repo.git", "github.com"),
        ("https://user@github.com/owner/repo", "github.com"),
        ("ssh://git@github.com-maria/owner/repo.git", "github.com-maria"),
        ("ssh://git@host:2222/owner/repo.git", "host"),
        ("/local/path/repo", None),
        ("C:\\repos\\thing", None),
    ],
)
def test_remote_host(url: str, host: str | None) -> None:
    assert remote_host(url) == host


def test_git_state_cleanliness() -> None:
    assert GitState("main", ()).is_clean
    assert not GitState("main", ("a.py",)).is_clean
