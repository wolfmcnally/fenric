# Install, update and remove Fenric

These are operator-run instructions, not actions performed by reading this page or by Fenric guidance setup. Choose one installation owner and retain the previously verified artifact and its hash before replacing it. Fenric requires Python 3.11+ and a native GUI; macOS is tested, native Linux/Windows behavior remains unverified. No other application is required for ordinary Fenric use. Tool-manager dependency resolution may need network access; obtain any required authorization separately.

## Install

Run the README's uv or pipx install command from the standalone root containing `pyproject.toml` in a checkout/source archive, or install its built wheel. Do not install an untrusted wheel merely because its filename matches. Verify the expected source/artifact identity through your release process. Source builds need the declared build backend; the repository's locked package builder verifies the archive inventory. Installing a command does not enable remote access or native harness approvals.

After installation, resolve the actual command, inspect its version/help, then follow [standard guidance setup](setup.md) on each host. Instruction coverage and native approval readiness are separate. A symlink proves a read path, not that a running harness reloaded it.

## Update a local-source or wheel installation

Stop creating new dialogs and let pending interactions settle or cancel through their supported owner. Preserve rollback and settings. For a uv-managed tool, run the following from the newly reviewed standalone root containing `pyproject.toml`; use the new verified wheel pathname in place of the dot for a wheel install:

```bash
uv tool install --reinstall .
```

For an existing pipx installation whose original source location/options are still correct:

```bash
pipx reinstall fenric
```

pipx reinstall first uninstalls and then reinstalls using its recorded source/options; it is not an atomic rollback mechanism. If that source is missing or points to the wrong candidate, stop and explicitly choose the intended reviewed source rather than blindly reinstall. Do not assume a registry upgrade publishes or selects the local reviewed candidate; unpublished release candidates may share the release version. Verify the artifact hash, not version alone.

After replacement, resolve the command again and inspect help/version and isolated import provenance. Long-running Python hosts must be restarted through their supported lifecycle to import new code; a changed executable selection does not update already-imported modules. Retain the old environment/artifact until checks and operator acceptance pass. Staged deployments that select a venv by symlink should build a new environment and switch only that existing approved selection, preserving rollback; the tool-manager commands above are not substitutes for that managed cutover procedure.

Rerun guidance preview/check. Apply only a reviewed unchanged plan with a new backup directory. Package replacement never silently adopts manually edited guidance, creates missing instruction paths, installs hooks or grants permissions. No automatic migration, credential access or authentication change is implied.

## Remove

If you want managed guidance removed, preview and apply its [owned-block removal](setup.md#removal-and-recovery) before uninstalling the command. Unowned or modified prose needs explicit review. To disable configured phone access while the command is still installed, the operator can run:

```bash
fenric remote off
```

That changes Fenric's enabled configuration, not unrelated Tailscale mappings, grants or login. It does not revoke a response that already settled. Let live interactions terminate before removing their software.

Use the manager that owns the installation:

```bash
uv tool uninstall fenric
```

or:

```bash
pipx uninstall fenric
```

For manually managed venv/symlink deployments, use the owning deployment procedure; these manager commands do not identify or safely remove arbitrary stages. Fenric has no general installer/uninstaller command. Package removal does not delete secret destinations, user instruction files, backups, remote configuration, chart caches or history. Any desired data/cache removal is a separate explicit decision; do not recursively delete directories based on these examples. Retain rollback artifacts until the operator decides they are no longer needed.

## Verification boundaries

The commands here were checked against available tool-manager help as of 2026-10-17; documentation tests do not install, upgrade or uninstall real tools. Clean-machine install/upgrade/uninstall acceptance and additional-platform GUI testing are separate release work. See [troubleshooting](troubleshooting.md) for missing commands, GUI and configuration failures.
