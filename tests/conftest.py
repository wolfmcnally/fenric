"""Shared fixtures.

No test inherits the caller's headless switch or chart cache, and none can
reach the native window: a test, or a mutation of the code under it, that
falls through to the default backend fails instead of opening a dialog.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FENRIC_HEADLESS", raising=False)
    monkeypatch.setenv("FENRIC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FENRIC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("FENRIC_DESKTOP_ONLY", raising=False)
    monkeypatch.setattr("fenric.backend.default_backend", _no_window)


def _no_window() -> None:
    raise AssertionError("a test reached the native window backend")


def isolate_fixture_git_signing(environment: dict[str, str]) -> None:
    """Fixture repositories never sign: the operator's signing setup is not under test.

    A machine that signs every commit hands that setting to each throwaway repository a
    test creates, and a signing agent asked to sign many commits at once refuses some of
    them. Git reads these variables after every configuration file, so the override holds
    whatever the machine configures. Entries a caller already supplied are kept, and a
    nested test run that inherits these does not add them twice.
    """
    count = int(environment.get("GIT_CONFIG_COUNT", "0") or "0")
    supplied = {environment.get(f"GIT_CONFIG_KEY_{index}") for index in range(count)}
    for key in ("commit.gpgsign", "tag.gpgsign"):
        if key in supplied:
            continue
        environment[f"GIT_CONFIG_KEY_{count}"] = key
        environment[f"GIT_CONFIG_VALUE_{count}"] = "false"
        count += 1
    environment["GIT_CONFIG_COUNT"] = str(count)


isolate_fixture_git_signing(os.environ)


# Methodology proofs import their helpers as top-level siblings; Fenric's own tests use
# package-relative imports through tests/__init__.py. Both resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Synthetic qualifying runs of the methodology proofs use persistent storage under the
# ignored .kickoff/ tree; Fenric's product tests keep pytest's own temporary directories.
METHODOLOGY_PROOFS = frozenset(
    [
        "render_execution_dashboard_fixture.py",
        "structured_fixtures.py",
        "test_check.py",
        "test_check_catalogs.py",
        "test_check_hooks_installed.py",
        "test_check_plan_concreteness.py",
        "test_check_plan_delivery.py",
        "test_check_receipt.py",
        "test_check_release_worktrees.py",
        "test_documentation.py",
        "test_execution_dashboard.py",
        "test_execution_telemetry.py",
        "test_install_hooks.py",
        "test_kickoff_config.py",
        "test_kickoff_control_plane.py",
        "test_kickoff_evidence.py",
        "test_kickoff_tree_id.py",
        "test_lessons.py",
        "test_log_control_plane.py",
        "test_mirror_parity.py",
        "test_multioperator.py",
        "test_mutation.py",
        "test_new_name.py",
        "test_pre_commit.py",
        "test_research_authority.py",
        "test_review_verdicts.py",
        "test_shell_syntax.py",
        "test_test_governance.py",
        "test_toolchain_callers.py",
        "test_toolchain_entrypoints.py",
        "test_treatise.py",
    ]
)


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory):
    if Path(str(request.node.fspath)).name not in METHODOLOGY_PROOFS:
        yield tmp_path_factory.mktemp(request.node.name[:30].replace("/", "_"), numbered=True)
        return
    root = Path(__file__).resolve().parents[1] / ".kickoff" / "proof-runtime"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="case-", dir=root) as directory:
        yield Path(directory)
