"""Secret capture through a real store subprocess."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fenric import store
from fenric.engine import run
from fenric.spec import SpecError, validate_spec

from .support import submits

# A store command: writes its standard input to <directory>/<name>, prints
# noise on both streams, and fails for the field named "refused".
STORE = (
    "import pathlib, sys, time\n"
    "directory, name = sys.argv[1], sys.argv[2]\n"
    "value = sys.stdin.read()\n"
    "print('STORE-NOISE', value)\n"
    "print('STORE-NOISE', value, file=sys.stderr)\n"
    "if name == 'slow':\n"
    "    time.sleep(30)\n"
    "if name == 'refused':\n"
    "    sys.exit(7)\n"
    "pathlib.Path(directory, name).write_text(value)\n"
)


def spec_for(tmp_path: Path, *names: str) -> dict[str, object]:
    script = tmp_path / "store.py"
    script.write_text(STORE)
    vault = tmp_path / "vault dir"
    vault.mkdir(exist_ok=True)
    return {
        "verb": "secret",
        "fields": list(names),
        "store": f"{sys.executable} {script} '{vault}' {{name}}",
    }


def test_each_filled_field_reaches_the_store_on_standard_input(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    result = run(
        spec_for(tmp_path, "api-key", "unused", "db.password"),
        backend=submits(f0="SENTINEL-one", f1="", f2="SENTINEL two\n"),
    )
    assert (result.status, result.exit_code, result.value, result.detail) == ("ok", 0, None, None)
    vault = tmp_path / "vault dir"
    assert sorted(path.name for path in vault.iterdir()) == ["api-key", "db.password"]
    assert (vault / "api-key").read_text() == "SENTINEL-one"
    assert (vault / "db.password").read_text() == "SENTINEL two\n"
    executable, named = tmp_path / "multi-call", tmp_path / "named-store"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import pathlib,sys\n"
        "assert pathlib.Path(sys.argv[0]).name == 'named-store'\n"
        "pathlib.Path(sys.argv[1]).write_text(sys.stdin.read())\n"
    )
    executable.chmod(0o700)
    named.symlink_to(executable)
    saved = tmp_path / "named-value"
    renderer = submits(f0="SENTINEL-named")
    linked = run(
        {"verb": "secret", "fields": ["key"], "store": f"'{named}' '{saved}'"},
        backend=renderer,
    )
    assert linked.status == "ok" and saved.read_text() == "SENTINEL-named"
    assert str(named.absolute()) in renderer.pages[0].html
    captured = capfd.readouterr()
    assert (
        captured.out == "" and captured.err == "fenric: remote: unavailable: not configured\n" * 2
    )


def test_a_failing_store_is_exit_3_naming_the_field_by_index(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    result = run(
        spec_for(tmp_path, "first", "refused", "third"),
        backend=submits(f0="SENTINEL-a", f1="SENTINEL-b", f2="SENTINEL-c"),
    )
    assert (result.status, result.exit_code) == ("error", 3)
    assert result.detail == "fields[1]: store command reported failure"
    assert result.value is None
    assert sorted(path.name for path in (tmp_path / "vault dir").iterdir()) == ["first"]
    captured = capfd.readouterr()
    assert "SENTINEL" not in captured.out + captured.err


def test_a_store_that_never_returns_is_exit_3_at_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "STORE_TIMEOUT_SECONDS", 0.5)
    result = run(spec_for(tmp_path, "slow"), backend=submits(f0="SENTINEL"))
    assert (result.exit_code, result.detail) == (
        3,
        "fields[0]: store command could not be completed",
    )


def test_a_secret_spec_cannot_supply_values_or_markup() -> None:
    for extra in ("value", "html", "placeholder"):
        with pytest.raises(SpecError) as raised:
            validate_spec(
                {"verb": "secret", "store": "s", "fields": [{"name": "k", extra: "SENTINEL"}]}
            )
        assert (raised.value.path, raised.value.problem) == ("fields[0]", "has an unknown field")
