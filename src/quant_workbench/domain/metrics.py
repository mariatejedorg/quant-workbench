"""Extracting named metrics from a run's console output."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from quant_workbench.domain.logs import strip_ansi
from quant_workbench.domain.project import MetricExtractorSpec, MetricKind, MetricPick
from quant_workbench.domain.runs import MetricValue

_NUMBER = re.compile(r"[-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?")
_UNICODE_MINUS = chr(0x2212)  # typographic minus, e.g. from formatted reports


def _to_float(text: str) -> float:
    """Parse ``'1,234.5'``, ``'+38.2%'`` or ``'-10.09'`` into a float.

    Thousands separators are removed; a trailing ``%`` is ignored (the value stays in
    percent points, so ``'12.5%'`` -> ``12.5``).
    """
    match = _NUMBER.search(text.replace(_UNICODE_MINUS, "-"))
    if match is None:
        raise ValueError(f"no number in {text!r}")
    return float(match.group().replace(",", ""))


def _convert(raw: str, kind: MetricKind) -> float | int | str:
    if kind is MetricKind.STR:
        return raw.strip()
    number = _to_float(raw)
    if kind is MetricKind.INT:
        return int(number)
    return number


def extract_metrics(
    lines: Iterable[str], extractors: Sequence[MetricExtractorSpec]
) -> tuple[MetricValue, ...]:
    """Apply each extractor to the output and return the metrics that matched.

    Every extractor is searched line by line. By default the **last** match wins: programs
    print a metric while working and a final summary at the end, and the summary is the one
    that counts. An extractor declared with ``pick = "first"`` keeps the first match instead
    (for a value that a later section of the output prints again in another context). An
    extractor that never matches, or matches text that is not a number, simply yields
    nothing; a missing metric must never fail a run.
    """
    cleaned = [strip_ansi(line) for line in lines]
    found: list[MetricValue] = []
    for extractor in extractors:
        compiled = re.compile(extractor.pattern)
        value: float | int | str | None = None
        for line in cleaned:
            match = compiled.search(line)
            if match is None:
                continue
            try:
                value = _convert(match.group(1), extractor.kind)
            except ValueError:
                continue
            if extractor.pick is MetricPick.FIRST:
                break
        if value is not None:
            found.append(MetricValue(extractor.name, value, extractor.unit))
    return tuple(found)
