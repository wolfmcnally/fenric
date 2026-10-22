"""Build Fenric's wheel and source archive and check what they contain.

Run with the project's Python:

    python package.py           # build into dist/, replacing what is there, and check
    python package.py --check   # build into a temporary directory and check

Both archives are built by the locked ``hatchling`` from the development
environment, so building needs no network and no build isolation. Their
members are then compared with ``package-inventory.txt``: a member that is
missing, one that is not listed, one that appears more than once (also after
normalizing), and one whose path is absolute or climbs out of the archive are
each reported by name, and the check exits 1.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
INVENTORY = ROOT / "package-inventory.txt"
DIST_INFO = "{dist-info}"
KINDS = ("wheel", "sdist")


def build(directory: Path, root: Path = ROOT) -> dict[str, Path]:
    """Build both archives from ``root`` into ``directory``; return them by kind."""
    directory.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [sys.executable, "-m", "hatchling", "build", "-d", str(directory)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout + completed.stderr)
        raise SystemExit(f"package: hatchling build failed with status {completed.returncode}")
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit(f"package: expected one wheel and one source archive in {directory}")
    return {"wheel": wheels[0], "sdist": sdists[0]}


def members(archives: dict[str, Path]) -> dict[str, list[str]]:
    """Each archive's member names, normalized so a version bump changes none.

    A name that escapes the archive is kept unnormalized and marked, so the
    comparison reports it rather than hiding it under a normalized name.
    """
    with zipfile.ZipFile(archives["wheel"]) as wheel:
        wheel_names = [info.filename for info in wheel.infolist() if not info.is_dir()]
    with tarfile.open(archives["sdist"]) as sdist:
        sdist_names = [info.name for info in sdist.getmembers() if not info.isdir()]
    prefix = archives["sdist"].name.removesuffix(".tar.gz") + "/"
    found: dict[str, list[str]] = {"wheel": [], "sdist": []}
    for name in wheel_names:
        if _escapes(name):
            found["wheel"].append(f"ESCAPES {name}")
            continue
        head, _, rest = name.partition("/")
        found["wheel"].append(f"{DIST_INFO}/{rest}" if head.endswith(".dist-info") else name)
    for name in sdist_names:
        if _escapes(name) or not name.startswith(prefix):
            found["sdist"].append(f"ESCAPES {name}")
            continue
        found["sdist"].append(name.removeprefix(prefix))
    return {kind: sorted(names) for kind, names in found.items()}


def read_inventory(path: Path = INVENTORY) -> dict[str, list[str]]:
    """The expected members, from ``[wheel]`` and ``[sdist]`` sections."""
    expected: dict[str, list[str]] = {kind: [] for kind in KINDS}
    section: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if section not in expected:
                raise SystemExit(f"package: unknown inventory section [{section}]")
            continue
        if section is None:
            raise SystemExit("package: inventory entry before any section")
        expected[section].append(line)
    return {kind: sorted(names) for kind, names in expected.items()}


def differences(expected: dict[str, list[str]], found: dict[str, list[str]]) -> list[str]:
    """Report lines for every member that is missing, unlisted, repeated or escaping."""
    report = []
    for kind in KINDS:
        report += [
            f"{kind}: {'listed' if side is expected else 'found'} more than once {name}"
            for side in (expected, found)
            for name, count in sorted(Counter(side[kind]).items())
            if count > 1
        ]
        wanted, present = set(expected[kind]), set(found[kind])
        report += [f"{kind}: missing {name}" for name in sorted(wanted - present)]
        report += [
            f"{kind}: {'escaping' if name.startswith('ESCAPES ') else 'unlisted'} "
            f"{name.removeprefix('ESCAPES ')}"
            for name in sorted(present - wanted)
        ]
    return report


def check(archives: dict[str, Path]) -> int:
    found = members(archives)
    report = differences(read_inventory(), found)
    if report:
        print("PACKAGE CHECK FAIL", file=sys.stderr)
        for line in report:
            print(f"  {line}", file=sys.stderr)
        return 1
    counts = ", ".join(f"{kind}: {len(found[kind])} members" for kind in KINDS)
    print(f"PACKAGE CHECK PASS ({counts})")
    return 0


def _escapes(name: str) -> bool:
    path = PurePosixPath(name)
    return path.is_absolute() or ".." in path.parts or "\\" in name


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    if arguments not in ([], ["--check"]):
        print("usage: package.py [--check]", file=sys.stderr)
        return 2
    if arguments:
        with tempfile.TemporaryDirectory() as scratch:
            return check(build(Path(scratch)))
    dist = ROOT / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    archives = build(dist)
    status = check(archives)
    for kind in KINDS:
        print(f"{kind}: {archives[kind].relative_to(ROOT).as_posix()}")
    return status


if __name__ == "__main__":
    sys.exit(main())
