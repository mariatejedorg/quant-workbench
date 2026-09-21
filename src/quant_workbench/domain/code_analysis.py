"""Static analysis of Python source text, using only the standard library (``ast``, ``tokenize``).

Everything here is a pure function of a source string, so the doctor's checkers stay thin
(read the files, call these, turn the results into findings) and each rule is unit-testable
with a three-line snippet.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import TypeGuard

from quant_workbench.domain.diagnostics import ExplainabilityScore

#: A function longer than this many lines is hard to explain in one breath.
SHORT_FUNCTION_LINES = 50
#: Cyclomatic complexity above this is more paths than a person keeps in their head.
COMPLEXITY_LIMIT = 10
#: Lines of explanation (comments and docstrings) per code line at which the density part of the
#: score is full: one line of explanation for every four lines of code.
TARGET_COMMENT_DENSITY = 0.25

_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
_DOCUMENTED_NODES = (ast.Module, ast.ClassDef, *_DEF_NODES)
_BRANCHES = (
    ast.If,
    ast.IfExp,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.match_case,
)
_SKIPPED_BY_SHALLOW_WALK = (ast.Lambda, ast.ClassDef, *_DEF_NODES)


def parse(source: str) -> ast.Module | None:
    """The syntax tree of ``source``, or ``None`` if it does not parse."""
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None


# ---------------------------------------------------------------- explainability
@dataclass(frozen=True, slots=True)
class FunctionMetrics:
    name: str
    line: int
    length: int
    complexity: int
    documented: bool
    public: bool


@dataclass(frozen=True, slots=True)
class FileMetrics:
    code_lines: int
    #: Lines that explain rather than execute: ``#`` comments and docstrings.
    comment_lines: int
    functions: tuple[FunctionMetrics, ...]
    #: Public modules/classes/functions that could carry a docstring, and how many do.
    documentable: int
    documented: int


def measure(source: str) -> FileMetrics | None:
    """Documentation and complexity measurements of one file (``None`` on a syntax error)."""
    tree = parse(source)
    if tree is None:
        return None
    docstring_lines = _docstring_lines(tree)
    code, comments = _line_kinds(source)
    functions = tuple(_function_metrics(tree))
    items = [(True, ast.get_docstring(tree) is not None)]  # the module itself
    items.extend(_documentable_items(tree.body))
    return FileMetrics(
        code_lines=len(code - docstring_lines),
        comment_lines=len(comments | docstring_lines),
        functions=functions,
        documentable=len(items),
        documented=sum(1 for _, has_doc in items if has_doc),
    )


def explainability(files: Iterable[FileMetrics]) -> ExplainabilityScore:
    """Combine per-file measurements into one 0-100 score (weights: 40/25/20/15)."""
    items = list(files)
    documentable = sum(f.documentable for f in items)
    documented = sum(f.documented for f in items)
    code_lines = sum(f.code_lines for f in items)
    comment_lines = sum(f.comment_lines for f in items)
    functions = [fn for f in items for fn in f.functions]

    docstrings = documented / documentable if documentable else 1.0
    density = comment_lines / code_lines if code_lines else 0.0
    short = _share(functions, lambda fn: fn.length <= SHORT_FUNCTION_LINES)
    simple = _share(functions, lambda fn: fn.complexity <= COMPLEXITY_LIMIT)
    score = 100 * (
        0.40 * docstrings
        + 0.25 * min(density / TARGET_COMMENT_DENSITY, 1.0)
        + 0.20 * short
        + 0.15 * simple
    )
    return ExplainabilityScore(
        score=round(score, 1),
        docstring_coverage=docstrings,
        comment_density=density,
        short_function_share=short,
        simple_function_share=simple,
        functions=len(functions),
        code_lines=code_lines,
    )


def _share(functions: list[FunctionMetrics], predicate: Callable[[FunctionMetrics], bool]) -> float:
    """The fraction of ``functions`` satisfying ``predicate`` (1.0 when there are none)."""
    if not functions:
        return 1.0
    return sum(1 for fn in functions if predicate(fn)) / len(functions)


def _docstring_lines(tree: ast.Module) -> set[int]:
    """Line numbers occupied by docstrings (they document the code, they are not code)."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, _DOCUMENTED_NODES):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def _line_kinds(source: str) -> tuple[set[int], set[int]]:
    """Physical lines that carry code tokens, and lines that carry a comment."""
    code: set[int] = set()
    comments: set[int] = set()
    ignored = {
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
        tokenize.ENCODING,
    }
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                comments.add(token.start[0])
            elif token.type not in ignored:
                code.update(range(token.start[0], token.end[0] + 1))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass  # keep what was read before the problem; parse() already vetted the file
    return code, comments


def _documentable_items(body: list[ast.stmt]) -> Iterator[tuple[bool, bool]]:
    """(is public, has docstring) for classes and functions outside any function body."""
    for node in body:
        if isinstance(node, (ast.ClassDef, *_DEF_NODES)):
            if _is_public(node.name):
                yield True, ast.get_docstring(node) is not None
            if isinstance(node, ast.ClassDef):
                yield from _documentable_items(node.body)


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def _function_metrics(tree: ast.Module) -> Iterator[FunctionMetrics]:
    for node in ast.walk(tree):
        if isinstance(node, _DEF_NODES):
            yield FunctionMetrics(
                name=node.name,
                line=node.lineno,
                length=(node.end_lineno or node.lineno) - node.lineno + 1,
                complexity=cyclomatic_complexity(node),
                documented=ast.get_docstring(node) is not None,
                public=_is_public(node.name),
            )


def cyclomatic_complexity(node: ast.AST) -> int:
    """McCabe complexity: one path, plus one for each decision point below ``node``."""
    total = 1
    for child in ast.walk(node):
        if isinstance(child, _BRANCHES):
            total += 1
        elif isinstance(child, ast.BoolOp):
            total += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            total += 1 + len(child.ifs)
    return total


# ------------------------------------------------------------- import collisions
@dataclass(frozen=True, slots=True)
class PathMutation:
    """A ``sys.path`` insertion that puts a *foreign* folder on the import path."""

    line: int
    scope: str


def uncleaned_foreign_path_inserts(tree: ast.Module) -> tuple[PathMutation, ...]:
    """``sys.path`` insertions of another folder with no ``sys.modules`` clean-up beside them.

    Every project of the portfolio has a top-level package called ``config`` (and most a
    ``src/data.py``). Loading a sibling project's code by putting *its* folder on
    ``sys.path`` makes ``import config`` resolve to whichever ``config`` was imported first:
    the classic silent-wrong-module bug. Inserting the project's own root
    (``Path(__file__)`` two levels up) is fine; inserting anything else is only safe if the
    same function also saves and restores ``sys.modules``, as the portfolio's loader does.
    """
    found: list[PathMutation] = []
    scopes: list[tuple[str, ast.AST]] = [("<module>", tree)]
    scopes += [(node.name, node) for node in ast.walk(tree) if isinstance(node, _DEF_NODES)]
    for name, scope in scopes:
        nodes = list(_walk_shallow(scope))
        cleans_modules = any(_is_sys_attribute(node, "modules") for node in nodes)
        if cleans_modules:
            continue
        for node in nodes:
            if _is_path_mutation(node) and node.args and _is_foreign_path(node.args[-1]):
                found.append(PathMutation(node.lineno, name))
    return tuple(sorted(found, key=lambda m: m.line))


def _walk_shallow(root: ast.AST) -> Iterator[ast.AST]:
    """``ast.walk`` that does not descend into nested functions, lambdas or classes."""
    stack: list[ast.AST] = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, _SKIPPED_BY_SHALLOW_WALK):
                stack.append(child)


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _is_sys_attribute(node: ast.AST, attribute: str) -> bool:
    return isinstance(node, ast.Attribute) and _dotted(node) == f"sys.{attribute}"


def _is_path_mutation(node: ast.AST) -> TypeGuard[ast.Call]:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("insert", "append")
        and _dotted(node.func.value) == "sys.path"
    )


#: Going up this many levels from ``src/x.py`` gives the project's own root.
_OWN_ROOT_ASCENTS = 2


def _is_foreign_path(expression: ast.expr) -> bool:
    """Whether ``expression`` is a path other than the project's own root.

    The own root is written as ``Path(__file__)`` plus exactly two steps up (``.parent``
    twice, ``parents[1]`` or two ``dirname`` calls). No mention of ``__file__`` at all
    (a variable, a literal) or three or more steps up means "somewhere else".
    """
    nodes = list(ast.walk(expression))
    if not any(isinstance(n, ast.Name) and n.id == "__file__" for n in nodes):
        return True
    return _ascents(nodes) >= _OWN_ROOT_ASCENTS + 1


def _ascents(nodes: list[ast.AST]) -> int:
    steps = 0
    for node in nodes:
        if (isinstance(node, ast.Attribute) and node.attr == "parent") or (
            isinstance(node, ast.Call) and _dotted(node.func).endswith("dirname")
        ):
            steps += 1
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
        ):
            steps += node.slice.value + 1
    return steps


# ------------------------------------------------------------- string literals
def string_literals(tree: ast.Module) -> Iterator[tuple[str, int]]:
    """Every string constant in the file with its line number."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value, node.lineno


def imports_module(tree: ast.Module, name: str) -> bool:
    """Whether the file imports ``name`` (or a submodule of it)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            a.name == name or a.name.startswith(f"{name}.") for a in node.names
        ):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (node.module == name or node.module.startswith(f"{name}."))
        ):
            return True
    return False


# --------------------------------------------------------------------- lint rules
@dataclass(frozen=True, slots=True)
class LintIssue:
    code: str
    line: int
    message: str


def lint(tree: ast.Module) -> tuple[LintIssue, ...]:
    """A handful of high-signal rules (bugs waiting to happen, not style)."""
    issues: list[LintIssue] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                issues.append(
                    LintIssue("bare-except", node.lineno, "bare `except:` also swallows Ctrl+C")
                )
            elif len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                issues.append(LintIssue("silent-except", node.lineno, "error swallowed by `pass`"))
        elif isinstance(node, _DEF_NODES):
            issues.extend(_mutable_defaults(node))
    issues.extend(_unused_imports(tree))
    return tuple(sorted(issues, key=lambda i: (i.line, i.code)))


def _mutable_defaults(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[LintIssue]:
    defaults = [*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)]
    for default in defaults:
        mutable = isinstance(default, ast.List | ast.Dict | ast.Set) or (
            isinstance(default, ast.Call)
            and isinstance(default.func, ast.Name)
            and default.func.id in ("list", "dict", "set")
        )
        if mutable:
            yield LintIssue(
                "mutable-default",
                default.lineno,
                f"`{node.name}` has a mutable default argument, shared between calls",
            )


def _unused_imports(tree: ast.Module) -> Iterator[LintIssue]:
    imported: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported[alias.asname or alias.name.split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            for alias in node.names:
                if alias.name != "*":
                    imported[alias.asname or alias.name] = node.lineno
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    exported = _dunder_all(tree)
    for name, line in imported.items():
        if name not in used and name not in exported:
            yield LintIssue("unused-import", line, f"`{name}` is imported but never used")


def _dunder_all(tree: ast.Module) -> set[str]:
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, ast.List | ast.Tuple)
        ):
            return {
                e.value
                for e in node.value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
    return set()


# ------------------------------------------------------------------------ secrets
_SECRET_PATTERNS: tuple[tuple[str, bool, re.Pattern[str]], ...] = (
    ("private-key", True, re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY")),
    ("aws-access-key", True, re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", True, re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack-token", True, re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    (
        "hardcoded-credential",
        False,
        re.compile(
            r"""(?i)\b(?:api[_-]?key|secret|token|passw(?:or)?d)\b\s*[:=]\s*['"][\w\-/+=]{16,}['"]"""
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class SecretHit:
    kind: str
    line: int
    #: ``True`` for formats that are unambiguous credentials; ``False`` for a heuristic match.
    certain: bool


def find_secrets(text: str) -> tuple[SecretHit, ...]:
    """Lines that look like a credential committed to source control."""
    hits: list[SecretHit] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, certain, pattern in _SECRET_PATTERNS:
            if pattern.search(line):
                hits.append(SecretHit(kind, number, certain))
                break
    return tuple(hits)
