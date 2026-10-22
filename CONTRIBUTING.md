# Contributing

Fenric is small on purpose: one runtime dependency (`pywebview`, imported only when a window opens), a strict spec, and a security model every change has to keep. Read the [security model](docs/security.md) before changing anything a dialog shows or returns.

All commands below run from this directory.

## Setup

[uv](https://docs.astral.sh/uv/) manages Python and the dependencies; `.python-version` pins the interpreter and `uv.lock` the versions.

```bash
uv sync --locked --managed-python
```

```bash
uv run --locked --managed-python fenric confirm --message "Proceed?" --timeout 30
```

## Tests and lint

```bash
uv run --locked --managed-python python -m pytest -q
```

```bash
uv run --locked --managed-python ruff check fenric tests preview build_assets.py package.py
```

```bash
uv run --locked --managed-python ruff format --check fenric tests preview build_assets.py package.py
```

The tests never open a window and need no network, Bun or browser. The render backend is a seam: tests drive it with the scripted double in `tests/support.py`, and a test that reaches the real window fails. A change that adds a field, an option or a verb also updates the [documentation](docs/), which the tests check against the code: every documented command must run, and every option and field must be documented.

## Browser bundles

The dialog's script is TypeScript under `assets/ts/`, compiled to bundles committed under `fenric/_assets/`, so an installed Fenric needs no build step. The build also writes `fenric/_assets/THIRD_PARTY_NOTICES.md` from the packages the bundler actually included. After editing a source, the npm manifest or lockfile, or the build script, rebuild with [Bun](https://bun.sh) on `PATH`:

```bash
uv run --locked --managed-python python build_assets.py
```

It installs the locked dependencies, type-checks with `tsc` in strict mode, bundles, and records a digest of every input and output; a test fails when that record no longer matches the files. To confirm the committed outputs are exactly what the sources compile to:

```bash
uv run --locked --managed-python python build_assets.py --check
```

## Preview harness

`preview/` drives every dialog's page, exactly as Fenric builds it, in Playwright's WebKit, the engine closest to the macOS window. It is outside the tests above. Installing it downloads WebKit into Playwright's per-user cache:

```bash
uv run --locked --managed-python python preview/run.py install
```

Check the buttons, the guards on the primary button, keyboard focus, the countdown ring and what each dialog hands back; run this after changing the page's script or style:

```bash
uv run --locked --managed-python python preview/run.py check
```

Generate a local gallery of every dialog in light and dark in `gallery/` (never committed; the chart sample needs `fenric setup-charts` first):

```bash
uv run --locked --managed-python python preview/run.py gallery
```

Open one sample in the real window:

```bash
uv run --locked --managed-python python preview/run.py show confirm
```

## Documentation

Everything the user documentation shows is captured from Fenric, never written by hand. Under each command in `README.md` and `docs/`, a capture marker (an HTML comment, invisible when rendered) says how to reproduce what follows: the answer the window gives, the expected exit status, any files to create first, and any file to show afterwards. `tests/test_docs.py` reruns every capture and fails if Fenric prints or writes anything different; the format is described at the top of that file. After a deliberate change to what Fenric prints, refresh every capture from the running code, then read the diff:

```bash
FENRIC_UPDATE_DOCS=1 uv run --locked --managed-python python -m pytest -q tests/test_docs.py
```

That run rewrites the captured fences and always fails, so it can never stand in for the check; run the tests again without it.

Every command whose capture answers a dialog shows that dialog's image, in `docs/images/`, rendered from the same command by the harness. Regenerate them after changing a dialog's look, an example, or a command shown with an image; the command reports which images changed:

```bash
uv run --locked --managed-python python preview/run.py docs
```

Documentation images use a synthetic receiving host and `/example/` file paths so publication does not disclose the rendering machine. Target validation still runs; these labels are illustrative, and no destination is written while rendering.

Images depend on the machine's fonts, so a regeneration on another machine can change bytes without changing anything a reader would notice; commit them when the change is real.

## Building the archives

```bash
uv run --locked --managed-python python package.py
```

builds the wheel and the source archive into `dist/` with the locked `hatchling` and compares their members with `package-inventory.txt`. A file added to or removed from the project is reported by name until the inventory lists it; `--check` builds into a temporary directory instead.

The public Python tutorial is in `docs/api.md`; its Python fences execute under isolated scripted backends in the existing documentation proof family. Tool-manager lifecycle commands in `docs/install.md` are checked as instructions, not run against real installations. Native walkthrough notation is deliberately not a production host factory.
