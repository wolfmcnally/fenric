"""The chart toolchain, driven through a controlled stand-in for Bun."""

from __future__ import annotations

from pathlib import Path

import pytest

from fenric import charts
from fenric.charts import ChartError, chart_cache_dir, compile_chart, setup_charts

from .support import install_bun_stub

SHIPPED = Path(charts.__file__).parent / "_toolchain"


@pytest.fixture
def bun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put the Bun stand-in first on PATH; return its directory."""
    directory = tmp_path / "bin"
    install_bun_stub(directory)
    monkeypatch.setenv("PATH", f"{directory}:/usr/bin:/bin")
    return directory


def test_an_unprovisioned_toolchain_names_the_setup_command(bun: Path) -> None:
    with pytest.raises(ChartError) as raised:
        compile_chart("d3.select(root);")
    assert str(raised.value) == "the chart toolchain is not provisioned; run `fenric setup-charts`"
    assert not (bun / "calls.log").exists()


def test_setup_copies_the_shipped_manifest_and_runs_a_frozen_install(bun: Path) -> None:
    setup_charts()
    cache = chart_cache_dir()
    for name in ("package.json", "bun.lock", "tsconfig.json"):
        assert (cache / name).read_bytes() == (SHIPPED / name).read_bytes()
    assert (bun / "calls.log").read_text().splitlines() == ["install --frozen-lockfile"]
    assert (cache / "node_modules").is_dir()
    assert compile_chart("ok();") == "(() => {ok();})();"


def test_a_failed_install_leaves_the_toolchain_unprovisioned(bun: Path) -> None:
    (bun / "fail-install").touch()
    with pytest.raises(ChartError) as raised:
        setup_charts()
    assert str(raised.value) == "the chart toolchain install failed"
    with pytest.raises(ChartError, match="not provisioned"):
        compile_chart("ok();")


def test_a_type_error_is_refused_and_compiler_output_is_discarded(
    bun: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    setup_charts()
    with pytest.raises(ChartError) as raised:
        compile_chart("const n: number = 'TYPE_ERROR SENTINEL';")
    assert str(raised.value) == "the chart code did not pass the type check"
    calls = (bun / "calls.log").read_text().splitlines()
    assert len(calls) == 2 and calls[1].startswith("x --no-install tsc -p ")
    captured = capfd.readouterr()
    assert captured.out == "" and captured.err == ""
    assert not list(chart_cache_dir().glob("job-*"))


def test_a_valid_chart_is_type_checked_then_transpiled(
    bun: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    setup_charts()
    assert compile_chart("draw(data);") == "(() => {draw(data);})();"
    calls = (bun / "calls.log").read_text().splitlines()
    assert [call.split()[0] for call in calls] == ["install", "x", "build"]
    assert "--target browser --format iife" in calls[2]
    captured = capfd.readouterr()
    assert "STUB-COMPILER-OUTPUT" not in captured.out + captured.err


def test_a_missing_bun_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ChartError) as raised:
        setup_charts()
    assert "Bun is required on PATH" in str(raised.value)


def test_a_tool_that_never_returns_is_refused_at_the_limit(
    bun: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_charts()
    (bun / "hang").touch()
    monkeypatch.setattr(charts, "TOOL_TIMEOUT_SECONDS", 0.5)
    with pytest.raises(ChartError) as raised:
        compile_chart("draw();")
    assert str(raised.value) == "the chart code did not pass the type check"


def test_an_unusable_cache_is_refused_as_a_chart_error(
    bun: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_charts()
    cache = chart_cache_dir()
    cache.chmod(0o500)
    try:
        with pytest.raises(ChartError) as raised:
            compile_chart("draw();")
    finally:
        cache.chmod(0o700)
    assert str(raised.value) == "the chart toolchain cache could not be used"

    blocked = tmp_path / "SENTINEL-not-a-directory"
    blocked.write_text("a file where the cache directory should be")
    monkeypatch.setenv("FENRIC_CACHE_DIR", str(blocked))
    with pytest.raises(ChartError) as raised:
        setup_charts()
    assert str(raised.value) == "the chart toolchain cache could not be used"
