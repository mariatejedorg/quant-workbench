"""The shipped failure-signature knowledge base, checked against real error text."""

from __future__ import annotations

from pathlib import Path

import pytest

from quant_workbench.domain.errors import ManifestError
from quant_workbench.domain.failures import match_signatures
from quant_workbench.infrastructure.resources import data_path
from quant_workbench.infrastructure.signatures import load_signatures

SIGNATURES = load_signatures(data_path("failure_signatures"))
BY_ID = {s.id: s for s in SIGNATURES}

#: Verbatim (trimmed) errors that actually occurred while building this portfolio.
REAL_FAILURES = {
    "ssl-certificate-verify": (
        "curl_cffi.requests.exceptions.CertificateVerifyError: Failed to perform, curl: (60) SSL "
        "certificate OpenSSL verify result: unable to get local issuer certificate (20)."
    ),
    "yfinance-rate-limit": (
        "yfinance.exceptions.YFRateLimitError: Too Many Requests. Rate limited. Try after a while."
    ),
    "network-timeout": (
        "requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='query1.finance.yahoo.com', "
        "port=443): Read timed out. (read timeout=10)"
    ),
    "tls-setup-failed": (
        "Cookie/crumb fetch failed (SSLError), continuing without crumb\n"
        "$AAPL: possibly delisted; no price data found  (period=3y)"
    ),
    "module-not-found": "ModuleNotFoundError: No module named 'scipy'",
    "calendar-mismatch": (
        "KeyError: \"[Timestamp('2023-07-04 00:00:00'), Timestamp('2023-09-04 00:00:00')] not in index\""
    ),
    "unicode-error": "UnicodeEncodeError: 'charmap' codec can't encode character '\\u2192' in position 3",
    "yfinance-no-data": "AAPLX: possibly delisted; no price data found  (period=3y)",
    "matplotlib-display": "_tkinter.TclError: no display name and no $DISPLAY environment variable",
    "output-file-locked": "PermissionError: [Errno 13] Permission denied: 'outputs/deck.pptx'",
}


def test_the_knowledge_base_loads_with_unique_ids() -> None:
    assert len(SIGNATURES) == len(BY_ID) >= len(REAL_FAILURES)
    assert all(s.advice for s in SIGNATURES), "every signature must tell the user what to do"


@pytest.mark.parametrize(("signature_id", "text"), REAL_FAILURES.items())
def test_each_real_failure_is_recognised(signature_id: str, text: str) -> None:
    matched = {s.id for s in match_signatures(text, SIGNATURES)}

    assert signature_id in matched


@pytest.mark.parametrize(
    ("signature_id", "text"),
    [
        (sid, text)
        for sid, text in REAL_FAILURES.items()
        if sid not in {"yfinance-rate-limit", "network-timeout"}
    ],
)
def test_permanent_failures_are_not_treated_as_transient(signature_id: str, text: str) -> None:
    """A retry cannot fix a missing package or a certificate problem: never mark those transient."""
    assert not any(s.transient for s in match_signatures(text, SIGNATURES))


def test_transient_failures_are_flagged_for_retry() -> None:
    assert BY_ID["yfinance-rate-limit"].transient
    assert BY_ID["network-timeout"].transient
    assert not BY_ID["ssl-certificate-verify"].transient


@pytest.mark.parametrize(
    "harmless",
    [
        "=== Datos descargados ===",
        "Universo: 8 activos | Sesiones: 1023",
        "Sharpe: 2.45 | drawdown máx. -10.09%",
        "warning: LF will be replaced by CRLF",
        "curl: (60) is discussed in the docs",  # cannot match a timeout code by accident
    ],
)
def test_ordinary_output_does_not_trigger_the_transient_signatures(harmless: str) -> None:
    matched = {s.id for s in match_signatures(harmless, SIGNATURES)}

    assert "network-timeout" not in matched
    assert "yfinance-rate-limit" not in matched
    assert "module-not-found" not in matched


def test_ssl_errors_are_not_confused_with_network_timeouts() -> None:
    matched = {s.id for s in match_signatures(REAL_FAILURES["ssl-certificate-verify"], SIGNATURES)}

    assert matched == {"ssl-certificate-verify"}


def test_ssl_signature_offers_the_ca_bundle_fix() -> None:
    assert BY_ID["ssl-certificate-verify"].fix == "copy-ca-bundle"
    assert BY_ID["module-not-found"].fix == "install-requirements"


# ----------------------------------------------------------------------- loader
def write(tmp: Path, name: str, body: str) -> Path:
    (tmp / name).write_text(body, encoding="utf-8")
    return tmp


def test_loader_reports_invalid_regexes_clearly(accented_root: Path) -> None:
    write(accented_root, "a.toml", '[[signature]]\nid = "x"\ntitle = "T"\npattern = "([unclosed"\n')

    with pytest.raises(ManifestError, match="invalid regular expression"):
        load_signatures(accented_root)


def test_loader_rejects_duplicate_ids_across_files(accented_root: Path) -> None:
    entry = '[[signature]]\nid = "dup"\ntitle = "T"\npattern = "x"\n'
    write(accented_root, "a.toml", entry)
    write(accented_root, "b.toml", entry)

    with pytest.raises(ManifestError, match="defined in both"):
        load_signatures(accented_root)


def test_loader_rejects_unknown_fields_and_bad_toml(accented_root: Path) -> None:
    write(
        accented_root, "a.toml", '[[signature]]\nid = "x"\ntitle = "T"\npattern = "x"\nbogus = 1\n'
    )
    with pytest.raises(ManifestError, match="bogus"):
        load_signatures(accented_root)

    (accented_root / "a.toml").write_text("not = [valid", encoding="utf-8")
    with pytest.raises(ManifestError, match="Cannot read"):
        load_signatures(accented_root)


def test_an_empty_directory_yields_no_signatures(accented_root: Path) -> None:
    assert load_signatures(accented_root) == ()


def test_the_specific_tls_diagnosis_outranks_the_misleading_no_data_symptom() -> None:
    """yfinance prints 'possibly delisted' when TLS fails; the root cause must be listed first."""
    matched = match_signatures(REAL_FAILURES["tls-setup-failed"], SIGNATURES)

    assert [s.id for s in matched][:2] == ["tls-setup-failed", "yfinance-no-data"]
