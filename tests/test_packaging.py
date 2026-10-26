"""The built wheel and source archive carry exactly what they should.

The archives are built once per session by ``package.py`` with the locked
``hatchling``, so these proofs need no network. Installing the wheel uses a
fresh standard-library virtual environment and pip with no index and no
dependencies: ``pywebview`` is imported only when a window opens, so the
command's help and version run without it. That is entry-point coverage; the
complete tool install, with its dependency, is a release-checklist step.
"""

from __future__ import annotations

import email
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import venv
import zipfile
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import package  # noqa: E402

# Files whose bytes in both archives must be the checked-out file's bytes.
SHIPPED = (
    "LICENSE",
    "fenric/_guidance/fenric/SKILL.md",
    "fenric/_guidance/fenric/guide.md",
    "fenric/_guidance/fenric-install/SKILL.md",
    "fenric/_guidance/fenric-install/guide.md",
    "fenric/_assets/THIRD_PARTY_NOTICES.md",
    "fenric/_assets/build-manifest.json",
    "fenric/_assets/chrome.js",
    "fenric/_assets/d3.js",
    "fenric/_protocol/interaction.schema.json",
    "fenric/_toolchain/bun.lock",
    "fenric/_toolchain/package.json",
    "fenric/_toolchain/tsconfig.json",
)
VERSION = tomllib.loads((PROJECT / "pyproject.toml").read_text())["project"]["version"]


@pytest.fixture(scope="module")
def archives(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    # Exercise the real build boundary even in a clean public checkout.
    local = PROJECT / ".kickoff"
    local.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="package-exclusion-", dir=local) as scratch:
        marker = Path(scratch) / "synthetic-private-evidence.txt"
        marker.write_text("Synthetic local evidence must never ship.\n", encoding="utf-8")
        # Exercise private and oversized media exclusion without using actual recordings.
        (Path(scratch) / "private-recording.wav").write_bytes(b"synthetic private audio")
        with (Path(scratch) / "large-recording.wav").open("wb") as large:
            large.truncate(17 * 1024 * 1024)
        return package.build(tmp_path_factory.mktemp("dist"))


def _wheel_files(wheel: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(wheel) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _sdist_files(sdist: Path) -> dict[str, bytes]:
    prefix = sdist.name.removesuffix(".tar.gz") + "/"
    with tarfile.open(sdist) as archive:
        return {
            info.name.removeprefix(prefix): archive.extractfile(info).read()
            for info in archive.getmembers()
            if info.isfile()
        }


def test_built_archives_match_the_inventory(archives: dict[str, Path]) -> None:
    found = package.members(archives)
    assert package.differences(package.read_inventory(), found) == []
    assert "fenric/cli.py" in found["wheel"]
    assert not any(name.startswith(("preview/", "tests/", "docs/")) for name in found["wheel"])
    ignored = (".kickoff/", "gallery/", "dist/", "node_modules/")
    assert not any(name.startswith(ignored) for name in found["sdist"])
    assert not any(name.startswith(".kickoff/") for name in found["wheel"])


def test_only_the_named_tiny_public_audio_demo_ships(archives: dict[str, Path]) -> None:
    media = {".wav", ".mp3", ".aac", ".m4a", ".flac", ".ogg", ".aiff"}
    wheel = _wheel_files(archives["wheel"])
    sdist = _sdist_files(archives["sdist"])
    assert not [name for name in wheel if Path(name).suffix.lower() in media]
    audio = {name: data for name, data in sdist.items() if Path(name).suffix.lower() in media}
    assert set(audio) == {"docs/examples/audio-demo/tone.wav"}
    demo = audio["docs/examples/audio-demo/tone.wav"]
    assert demo == (PROJECT / "docs/examples/audio-demo/tone.wav").read_bytes()
    assert len(demo) == 9644
    assert not any("private-recording" in name or "large-recording" in name for name in sdist)
    for name in ("generate.py", "index.html", "PROVENANCE.md"):
        relative = "docs/examples/audio-demo/" + name
        assert sdist[relative] == (PROJECT / relative).read_bytes()


def test_a_member_outside_the_archive_is_reported(tmp_path: Path) -> None:
    wheel, sdist = tmp_path / "fenric-0.1.0-py3-none-any.whl", tmp_path / "fenric-0.1.0.tar.gz"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("fenric/__init__.py", "")
        archive.writestr("../outside.py", "")
    with tarfile.open(sdist, "w:gz") as archive:
        for name in ("fenric-0.1.0/README.md", "elsewhere/notes.md"):
            info = tarfile.TarInfo(name)
            archive.addfile(info, io.BytesIO(b""))
    found = package.members({"wheel": wheel, "sdist": sdist})
    expected = {"wheel": ["fenric/__init__.py"], "sdist": ["README.md"]}
    assert package.differences(expected, found) == [
        "wheel: escaping ../outside.py",
        "sdist: escaping elsewhere/notes.md",
    ]


def test_a_member_found_twice_is_reported(tmp_path: Path) -> None:
    wheel, sdist = tmp_path / "fenric-0.1.0-py3-none-any.whl", tmp_path / "fenric-0.1.0.tar.gz"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("fenric-0.1.0.dist-info/METADATA", "")
        archive.writestr("fenric-0.2.0.dist-info/METADATA", "")
    with tarfile.open(sdist, "w:gz") as archive:
        archive.addfile(tarfile.TarInfo("fenric-0.1.0/README.md"), io.BytesIO(b""))
    found = package.members({"wheel": wheel, "sdist": sdist})
    expected = {"wheel": ["{dist-info}/METADATA"], "sdist": ["README.md"]}
    assert package.differences(expected, found) == [
        "wheel: found more than once {dist-info}/METADATA"
    ]


def test_archived_assets_and_licenses_are_the_checked_out_files(
    archives: dict[str, Path],
) -> None:
    wheel = _wheel_files(archives["wheel"])
    sdist = _sdist_files(archives["sdist"])
    dist_info = f"fenric-{VERSION}.dist-info"
    for name in SHIPPED:
        source = (PROJECT / name).read_bytes()
        assert sdist[name] == source, f"sdist {name}"
        if name != "LICENSE":
            assert wheel[name] == source, f"wheel {name}"
    assert wheel[f"{dist_info}/licenses/LICENSE"] == (PROJECT / "LICENSE").read_bytes()
    notices = "fenric/_assets/THIRD_PARTY_NOTICES.md"
    assert wheel[f"{dist_info}/licenses/{notices}"] == (PROJECT / notices).read_bytes()


def test_wheel_metadata_declares_every_license_it_carries(archives: dict[str, Path]) -> None:
    metadata = email.message_from_bytes(
        _wheel_files(archives["wheel"])[f"fenric-{VERSION}.dist-info/METADATA"]
    )
    notices = (PROJECT / "fenric/_assets/THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    bundled = set(re.findall(r"^License: (\S+)$", notices, re.MULTILINE))
    declared = set(metadata["License-Expression"].split(" AND "))
    assert declared == {"MIT"} | bundled
    assert sorted(metadata.get_all("License-File")) == [
        "LICENSE",
        "fenric/_assets/THIRD_PARTY_NOTICES.md",
    ]


def test_the_installed_command_runs_from_any_directory(
    archives: dict[str, Path], tmp_path: Path
) -> None:
    environment = tmp_path / "env"
    venv.EnvBuilder(with_pip=True, symlinks=True).create(environment)
    python = environment / "bin" / "python"
    subprocess.run(
        [
            python,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--no-deps",
            "--no-index",
            "--disable-pip-version-check",
            archives["wheel"],
        ],
        check=True,
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    minimal = {"PATH": "/usr/bin:/bin", "HOME": str(elsewhere)}
    command = environment / "bin" / "fenric"
    help_run = subprocess.run(
        [command, "--help"], cwd=elsewhere, env=minimal, capture_output=True, text=True
    )
    assert help_run.returncode == 0, help_run.stderr
    assert help_run.stdout.startswith("usage: fenric ")
    assert "setup-charts" in help_run.stdout
    assert "interact" in help_run.stdout
    assert "fenric guidance read" in help_run.stdout
    for arguments, filename, bundle in (
        ([], "guide.md", "fenric"),
        (["--skill"], "SKILL.md", "fenric"),
        (["--installer"], "guide.md", "fenric-install"),
        (["--installer", "--skill"], "SKILL.md", "fenric-install"),
    ):
        read_run = subprocess.run(
            [command, "guidance", "read", *arguments],
            cwd=elsewhere,
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
        )
        guide = PROJECT / "fenric/_guidance" / bundle / filename
        assert (read_run.returncode, read_run.stdout, read_run.stderr) == (
            0,
            guide.read_text(),
            "",
        )
    lifecycle = subprocess.run(
        [command, "lifecycle", "plan", "--scope", "global", "--executable", command],
        cwd=elsewhere,
        env=minimal,
        capture_output=True,
        text=True,
    )
    assert lifecycle.returncode == 4 and lifecycle.stderr == ""
    observed_plan = json.loads(lifecycle.stdout)
    assert observed_plan["complete"] is True and observed_plan["status"] == "blocked"
    from fenric import lifecycle as implementation

    for name in implementation.SKILLS:
        source = implementation.tree(PROJECT / "fenric/_guidance" / name, resources=True)
        assert observed_plan["desired"][name]["bundle_sha256"] == source["bundle_sha256"]
    assert not (elsewhere / ".claude").exists()
    assert not (elsewhere / ".codex").exists()
    bare_run = subprocess.run([command], cwd=elsewhere, env=minimal, capture_output=True, text=True)
    assert (bare_run.returncode, bare_run.stdout, bare_run.stderr) == (0, help_run.stdout, "")
    invalid_run = subprocess.run(
        [command, "--not-a-valid-option"],
        cwd=elsewhere,
        env=minimal,
        capture_output=True,
        text=True,
    )
    assert invalid_run.returncode == 3 and invalid_run.stdout == ""
    assert invalid_run.stderr == "fenric: error: invalid invocation; run `fenric --help`\n"
    version_run = subprocess.run(
        [command, "--version"], cwd=elsewhere, env=minimal, capture_output=True, text=True
    )
    assert (version_run.returncode, version_run.stdout) == (0, f"fenric {VERSION}\n")
    assert os.listdir(elsewhere) == []
