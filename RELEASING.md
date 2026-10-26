# Releasing

This checklist prepares and publishes the standalone Fenric project. Version 0.1.0 is the first public release candidate; preparation does not mean it is published. Commands run from the standalone root containing `pyproject.toml`. Publication, repository visibility/history changes and security settings require the operator's explicit authorization.

## 1. Freeze the source and release notes

- [ ] Select the exact reviewed source and version. The version in `pyproject.toml` is authoritative; `fenric --version` reads installed metadata. For a future version change, update the lock and README wheel filename together.
- [ ] Consolidate the corresponding section of `CHANGELOG.md`. Keep `— unreleased` until publication; do not invent a publication date. This section supplies the release notes.
- [ ] Check the public CLI/API, platform limitations, optional integrations and install/update/rollback instructions against the source. Do not infer hook activation, native approval authority or additional-platform GUI acceptance from installation.
- [ ] Finish required review and source checks with the test, lint, asset and packaging checks described here before committing its explicit changed paths. Verify that the resulting committed tree matches the checked candidate; do not push as a side effect of preparation.

## 2. Prepare the exact public tree (first-release export)

- [ ] Audit the proposed public files, metadata and complete proposed Git history for credentials, private paths/names, conversations and references to unrelated projects. Pattern matches are leads requiring context, not proof of either exposure or absence. Preserve findings privately; never put raw sensitive matches into public reports.
- [ ] For the first release, export only the explicitly inventoried standalone project members into a new directory. Preserve their modes, reject duplicate/escaping names and escaping links, and exclude the private development root, ignored files, machine configuration, execution evidence and old `.git` directory. Compare every exported byte with the reviewed source.
- [ ] If development history is unsuitable for publication, retain the plain export until its standalone checks below pass. Then prepare a separate initial local repository on `master` from those exact verified members. Preserve original history; do not replace remote history, create a remote or make anything public during preparation.
- [ ] After the standalone checks pass and initial history is prepared, re-audit all objects and messages in the actual proposed public history, not merely the latest checkout. Check images and other binary members as well as text; verify metadata and archive member names. Record an explicit inventory and its digest privately.
- [ ] Use the public root for the following tests and builds. Package URLs must address this root, with no private development-directory prefix.

## 3. Validate and build

- [ ] Run the standalone test suite; tests never open a native window:

```bash
uv run --locked --managed-python python -m pytest -q
```

- [ ] Check lint and formatting using the commands in [Contributing](CONTRIBUTING.md). All required checks must pass on the exact source tree being released.
- [ ] Reuse browser and live acceptance evidence bound to the same source when browser code and dialog behavior are unchanged. Otherwise check the committed assets and real browser behavior as described in Contributing. Do not label synthetic checks as physical-device acceptance.
- [ ] Build archives in the public root and check their complete inventories; expect `PACKAGE CHECK PASS`:

```bash
uv run --locked --managed-python python package.py
```

The script's plain mode replaces only this root's `dist/`. Its `--check` mode uses temporary output. An owning release script may instead import `package.build(new_output_directory)` and `package.check(archives)` to retain artifacts outside the source tree; no new command option is needed.

- [ ] Record SHA256 for the wheel, source archive and standalone export. Verify archive types, duplicate names, traversal, links, complete inventory and shipped source/license bytes. Re-scan the final archives, including generated metadata, for unintended content.
- [ ] Review actual dependency metadata/license files and the complete bundled chart notices. Retain their existing terms; do not infer redistribution permission or legal certification from a name-only scan. Fenric's wheel carries its MIT license and `fenric/_assets/THIRD_PARTY_NOTICES.md`; runtime dependencies are resolved separately.

## 4. Prove isolated installation and recovery

- [ ] Start in a new scratch directory outside the source. Set `WHEEL` to the verified wheel's absolute path, then exercise the documented tool install without replacing the operator's real tool:

```bash
UV_TOOL_DIR="$PWD/tools" UV_TOOL_BIN_DIR="$PWD/bin" uv tool install "$WHEEL"
```

For an approved cached-only qualification, add `--offline`, the existing qualified interpreter and constraints for the unchanged pinned runtime dependencies; stop on a cache miss. Do not download or upgrade a toolchain merely to finish preparation.

- [ ] Resolve this scratch command and check version, bare/help and relevant verb help from outside the source. Use the scratch environment's isolated Python to import `fenric` and `webview`; compare all installed Fenric members with the verified wheel and check dependency compatibility. Version alone cannot distinguish unpublished candidates.
- [ ] Follow [guidance setup](docs/setup.md) preview/apply/check/removal against synthetic instruction files and synthetic symlink aliases only. Keep guidance coverage separate from native adapter readiness. Never use real global instructions for release tests.
- [ ] Preserve the previous installed environment/artifact and settings. Read [install/update/remove](docs/install.md) for manager ownership and rollback. Do not replace software underneath a pending dialog; a changed CLI selection does not replace modules already imported by another process. Any real lifecycle restart is a separately coordinated deployment step.
- [ ] If runtime behavior changed, perform the relevant operator-visible acceptance with harmless content. Otherwise retain the existing acceptance evidence and its exact limits; additional-platform GUI and exhaustive mobile clipboard/IME checks remain unclaimed.

## 5. Operator publication decision

- [ ] Review the final version, release notes, exact source/history and artifact hashes, audience, repository target, checks and remaining limitations.
- [ ] Resolve how the standalone history reaches the public target. An existing private repository may contain old history and internal staging assets: changing its visibility alone can expose them. A fresh snapshot does not remove those remote objects/assets. Any history replacement, deletion or different repository target requires its own explicit decision.
- [ ] Enable and verify GitHub private vulnerability reporting before offering the public reporting path in `SECURITY.md`. This checklist does not enable it.
- [ ] At explicit publication authorization, make the actual release date the final source edit in `CHANGELOG.md`, rerun affected checks and rebuild/re-hash both archives from that exact tree. Verify the resulting release commit matches the checked tree. Publish only that approved history, tag that dated release commit as `v<version>`, and attach only its newly verified archives. Preparation artifacts marked unreleased are not upload-ready. A changed repository target/layout also requires correcting metadata URLs and rebuilding.
- [ ] PyPI publication is optional and separate. Confirm namespace ownership, credentials and artifact identity before any upload; no registry publication is implied by a GitHub release.
- [ ] After publication, add a new `[Unreleased]` section through the normal checked source workflow. Preserve the final candidate receipts and rollback artifacts.

## Media guidance release alignment

- [ ] Before releasing selected-WAV support, choose the next version under this checklist, update every unreleased/source-only statement in README, docs/media.md, docs/commands.md and the packaged agent guide to the actual release capability, and regenerate version-bound captures. Never upload a development wheel under the already-published 0.2.0 identity.
- [ ] Retain only the tiny original docs/examples/audio-demo/tone.wav as source-archive demo audio; no audio in the wheel. Re-run the package media allowlist/exclusion and reproducible-demo checks. Keep private recordings and evidence outside publication inputs.
