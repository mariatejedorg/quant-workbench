"""`qw sync`: clone the registry's repositories into a workspace (local origins, no network)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quant_workbench.application.catalog import Catalog, ProjectCatalog
from quant_workbench.application.sync import SyncService, SyncStatus
from quant_workbench.cli.main import app
from quant_workbench.domain.errors import GitError, ManifestError
from quant_workbench.infrastructure.git_cli import GitCli
from quant_workbench.infrastructure.workspace import FileSystemWorkspace

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_git_config(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A throw-away global git config: no test can read or change the real one.

    Network tests keep the machine's git configuration: on Windows the system config selects
    the certificate backend (schannel), without which HTTPS clones fail on many machines.
    """
    if request.node.get_closest_marker("network"):
        return
    config = tmp_path / "global-gitconfig"
    config.write_text("[user]\n\temail = t@example.org\n\tname = Test\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_origin(base: Path, name: str) -> Path:
    """A bare repository with one commit: what a GitHub remote looks like to ``git clone``."""
    work = base / f"{name}-work"
    (work / "src").mkdir(parents=True)
    (work / "src" / "main.py").write_text(f'print("{name}")\n', encoding="utf-8")
    git("init", "-q", "-b", "main", cwd=work)
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "first", cwd=work)
    bare = base / f"{name}.git"
    git("clone", "-q", "--bare", str(work), str(bare), cwd=base)
    return bare


def registry_entry(slug: str, folder: str, url: str | None) -> str:
    url_line = f"repo_url = '{url}'\n" if url else ""
    return (
        f'schema_version = 1\nslug = "{slug}"\ntitle = "{slug}"\nfolder = "{folder}"\n'
        f'category = "Test"\norder = 10\ndescription = "d"\n{url_line}'
    )


@pytest.fixture
def origins(tmp_path: Path) -> Path:
    base = tmp_path / "origins"
    base.mkdir()
    return base


def catalog_for(workspace: Path, registry: Path) -> tuple[ProjectCatalog, Catalog]:
    projects = ProjectCatalog(FileSystemWorkspace(registry_dir=registry))
    return projects, projects.load(workspace)


@pytest.fixture
def registry(tmp_path: Path, origins: Path) -> Path:
    """Two projects whose local folder names differ from their repository names."""
    directory = tmp_path / "registry"
    directory.mkdir()
    for slug, folder in (("alpha", "proyecto-1-análisis"), ("beta", "proyecto-2-opciones")):
        url = str(make_origin(origins, slug))
        (directory / f"{slug}.toml").write_text(registry_entry(slug, folder, url), encoding="utf-8")
    return directory


def service(origins: Path) -> SyncService:
    # The origins are local paths, so they are trusted explicitly for the test.
    return SyncService(GitCli(), trusted_prefixes=(str(origins),))


def test_an_empty_workspace_is_filled_with_the_registry_folder_names(
    accented_root: Path, registry: Path, origins: Path
) -> None:
    projects, catalog = catalog_for(accented_root, registry)
    assert len(catalog.missing) == 2

    outcomes = service(origins).sync(catalog)

    assert [o.status for o in outcomes] == [SyncStatus.CLONED, SyncStatus.CLONED]
    assert (accented_root / "proyecto-1-análisis" / "src" / "main.py").is_file()
    assert (accented_root / "proyecto-2-opciones" / ".git").is_dir()
    after = projects.load(accented_root)
    assert after.missing == ()
    assert {p.slug for p in after.projects} == {"alpha", "beta"}


def test_syncing_again_has_nothing_to_do(
    accented_root: Path, registry: Path, origins: Path
) -> None:
    projects, catalog = catalog_for(accented_root, registry)
    service(origins).sync(catalog)

    assert service(origins).sync(projects.load(accented_root)) == ()


def test_an_existing_project_is_never_touched(
    accented_root: Path, registry: Path, origins: Path
) -> None:
    mine = accented_root / "proyecto-1-análisis" / "src"
    mine.mkdir(parents=True)
    (mine / "main.py").write_text("# my local edits\n", encoding="utf-8")
    _, catalog = catalog_for(accented_root, registry)

    outcomes = service(origins).sync(catalog, ["alpha", "beta"])

    assert [(o.spec.slug, o.status) for o in outcomes] == [
        ("alpha", SyncStatus.PRESENT),
        ("beta", SyncStatus.CLONED),
    ]
    assert (mine / "main.py").read_text(encoding="utf-8") == "# my local edits\n"


def test_a_dry_run_reports_without_cloning(
    accented_root: Path, registry: Path, origins: Path
) -> None:
    _, catalog = catalog_for(accented_root, registry)

    outcomes = service(origins).sync(catalog, dry_run=True)

    assert {o.status for o in outcomes} == {SyncStatus.WOULD_CLONE}
    assert list(accented_root.iterdir()) == []


def test_outcomes_are_reported_one_by_one_as_they_happen(
    accented_root: Path, registry: Path, origins: Path
) -> None:
    _, catalog = catalog_for(accented_root, registry)
    seen: list[str] = []

    service(origins).sync(catalog, on_outcome=lambda o: seen.append(o.spec.slug))

    assert seen == ["alpha", "beta"]


def test_only_https_is_cloned_by_default(
    accented_root: Path, registry: Path, origins: Path
) -> None:
    _, catalog = catalog_for(accented_root, registry)

    outcomes = SyncService(GitCli()).sync(catalog)  # default policy: https:// only

    assert {o.status for o in outcomes} == {SyncStatus.REFUSED}
    assert "not an HTTPS URL" in outcomes[0].detail
    assert list(accented_root.iterdir()) == []


def test_a_failure_does_not_stop_the_others(
    accented_root: Path, registry: Path, origins: Path
) -> None:
    (registry / "gamma.toml").write_text(
        registry_entry("gamma", "proyecto-0-roto", str(origins / "does-not-exist.git")),
        encoding="utf-8",
    )
    (registry / "delta.toml").write_text(
        registry_entry("delta", "proyecto-9-sin-url", None), encoding="utf-8"
    )
    _, catalog = catalog_for(accented_root, registry)

    outcomes = {o.spec.slug: o for o in service(origins).sync(catalog)}

    assert outcomes["alpha"].status is SyncStatus.CLONED
    assert outcomes["beta"].status is SyncStatus.CLONED
    assert outcomes["gamma"].status is SyncStatus.FAILED
    assert outcomes["delta"].status is SyncStatus.REFUSED
    assert "no repository URL" in outcomes["delta"].detail
    assert not outcomes["gamma"].ok
    assert not (accented_root / "proyecto-0-roto" / "src").exists()


def test_a_folder_with_a_registry_name_counts_as_present_and_is_left_alone(
    accented_root: Path, registry: Path, origins: Path
) -> None:
    mine = accented_root / "proyecto-1-análisis"
    mine.mkdir()
    (mine / "notes.txt").write_text("keep me\n", encoding="utf-8")
    _, catalog = catalog_for(accented_root, registry)

    outcomes = {o.spec.slug: o for o in service(origins).sync(catalog)}

    assert set(outcomes) == {"beta"}  # alpha is not even attempted
    assert (mine / "notes.txt").read_text(encoding="utf-8") == "keep me\n"


def test_the_adapter_itself_refuses_to_clone_into_a_non_empty_folder(
    accented_root: Path, origins: Path
) -> None:
    target = accented_root / "taken"
    target.mkdir()
    (target / "notes.txt").write_text("keep me\n", encoding="utf-8")

    with pytest.raises(GitError, match="not empty"):
        GitCli().clone(str(make_origin(origins, "alpha")), target)

    assert [p.name for p in target.iterdir()] == ["notes.txt"]


def test_an_unknown_slug_is_an_expected_failure(
    accented_root: Path, registry: Path, origins: Path
) -> None:
    _, catalog = catalog_for(accented_root, registry)

    with pytest.raises(ManifestError, match="not in the registry"):
        service(origins).sync(catalog, ["nope"])


def test_a_url_can_never_be_read_as_a_git_option(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"

    with pytest.raises(GitError):
        GitCli().clone(f"--upload-pack=touch {marker}", tmp_path / "dest")

    assert not marker.exists()


def test_the_cli_previews_the_real_registry_without_cloning(tmp_path: Path) -> None:
    workspace = tmp_path / "new workspace" / "Quant - María"  # does not exist yet

    result = runner.invoke(
        app, ["--home", str(tmp_path / "home"), "sync", "--dry-run", "-w", str(workspace)]
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("would clone") == 10
    assert list(workspace.iterdir()) == []  # created, but nothing cloned into it


@pytest.mark.network
@pytest.mark.slow
def test_a_real_clone_from_github(tmp_path: Path) -> None:
    workspace = tmp_path / "Quant - María"

    result = runner.invoke(
        app,
        ["--home", str(tmp_path / "home"), "sync", "market-data-analytics", "-w", str(workspace)],
    )

    assert result.exit_code == 0, result.output
    assert (workspace / "proyecto-1-analisis-mercado" / "src" / "main.py").is_file()
    assert "cloned" in result.output
