"""Syntax highlighting data from Pygments (Qt-free): which spans of each line get which colour."""

from __future__ import annotations

from dataclasses import dataclass

from pygments.lexer import Lexer
from pygments.lexers import TextLexer, get_lexer_for_filename
from pygments.token import Token, _TokenType
from pygments.util import ClassNotFound

from quant_workbench.ui.theme import Tokens

#: A span of one line: ``(start column, length, category)``.
Span = tuple[int, int, str]

#: Files above this many characters are shown without highlighting (a 5 MB log is not code).
MAX_HIGHLIGHTED_CHARS = 400_000

_CATEGORIES: tuple[tuple[_TokenType, str], ...] = (
    (Token.Comment, "comment"),
    (Token.Literal.String.Doc, "comment"),
    (Token.Literal.String, "string"),
    (Token.Literal.Number, "number"),
    (Token.Name.Decorator, "decorator"),
    (Token.Name.Function, "function"),
    (Token.Name.Class, "function"),
    (Token.Name.Builtin, "builtin"),
    (Token.Keyword, "keyword"),
    (Token.Operator.Word, "keyword"),
)


def category_of(token: _TokenType) -> str | None:
    """The colour category of a Pygments token type, or ``None`` for plain text."""
    for parent, category in _CATEGORIES:
        if token in parent:
            return category
    return None


def lexer_for(filename: str) -> Lexer:
    try:
        return get_lexer_for_filename(filename, stripnl=False)
    except ClassNotFound:
        return TextLexer(stripnl=False)


def spans_by_line(source: str, filename: str) -> list[list[Span]]:
    """For every line of ``source``, the coloured spans on it.

    The whole file is lexed once (so multi-line strings and docstrings are coloured
    correctly) and the tokens are cut at line boundaries.
    """
    lines: list[list[Span]] = [[]]
    if len(source) > MAX_HIGHLIGHTED_CHARS:
        return lines * (source.count("\n") + 1)
    column = 0
    for token, value in lexer_for(filename).get_tokens(source):
        category = category_of(token)
        pieces = value.split("\n")
        for number, piece in enumerate(pieces):
            if number > 0:
                lines.append([])
                column = 0
            if category and piece.strip():
                lines[-1].append((column, len(piece), category))
            column += len(piece)
    return lines


@dataclass(frozen=True, slots=True)
class SyntaxColours:
    """One colour per category, for a theme."""

    keyword: str
    string: str
    comment: str
    number: str
    function: str
    builtin: str
    decorator: str

    def of(self, category: str) -> str:
        return getattr(self, category)


def syntax_colours(tokens: Tokens) -> SyntaxColours:
    """Colours that keep at least 4.5:1 contrast on the theme's surface."""
    if tokens.name == "dark":
        return SyntaxColours(
            keyword="#c586c0",
            string="#ce9178",
            comment="#8a9a7b",
            number="#b5cea8",
            function="#dcdcaa",
            builtin="#4ec9b0",
            decorator="#d7ba7d",
        )
    return SyntaxColours(
        keyword="#7b3fa8",
        string="#a3421a",
        comment="#5a6b4a",
        number="#1a6b3c",
        function="#7a5b00",
        builtin="#0b6e7a",
        decorator="#8a5a00",
    )
