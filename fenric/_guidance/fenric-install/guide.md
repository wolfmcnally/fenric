# Read-only Fenric lifecycle planning

Start with the receiving host's command resolution and relevant help. Inventory inspects the selected executable and bounded neighboring metadata without executing that executable, importing its package, invoking a manager or opening a window. Use an explicit executable when PATH does not identify the intended installation. The lifecycle commands themselves run in the environment of the Fenric command you invoked; its version is reported separately from the inspected runtime.

```bash
fenric lifecycle inventory
```

Select an existing home or project explicitly when needed. Inventory considers only the named Fenric skills beneath conventional `.claude/skills`, `.codex/skills` and `.agents/skills` locations, without searching parent projects. These are filesystem candidates, not a claim about a harness's configured discovery precedence. Custom locations and live harness loading remain unverified. Without an explicit project, project shadowing is unobserved.

```bash
fenric lifecycle plan --scope global --target claude
```

```bash
fenric lifecycle plan --scope project --project . --target agents
```

`--target` can be repeated; its default selects `claude` and `codex`. It chooses which conventional roots receive proposed skill operations, not which harness will actually load them. The `.agents/skills` candidate is selected with `agents`. Missing skill parent directories block an add: the planner never creates them. A scope conflict blocks a change even if a caller assumes one harness would override the other. Resolve the intended scope through the operator's supported configuration; do not silently install at every discovered path.

The versioned plan is deterministic JSON with a SHA256 over its normalized inputs and exact observations. It proposes only skill add, update, remove, no-op or blocked operations. Runtime changes are manual-only and not proposed. There is no apply endpoint and no shell command array to execute. A plan is evidence for review, never authority, an approval token or a transaction. Reobserve before acting later; changed files, aliases, identities, bundle contents or scope require a fresh plan.

The current application package and a skill are not interchangeable versions. A skill's optional plain top-level `version:` frontmatter scalar is reported only when declared; otherwise its version is absent/null. The containing package version and full member manifest/content digest still identify its source. An installed file or correct link does not prove a running application or harness loaded it. Runtime artifact hashes come only from bounded local direct-URL metadata and a matching local wheel, not from guessing from version or filename. Manager records are untrusted local metadata, not an authenticated owner assertion.

## Ownership and conflicts

Skills-first setup means copying the complete passive skill directory through the selected harness's supported manual procedure after authorization, including its relative guide. Installing a wheel merely packages the resources; it does not register them as skills. A manual copy without a matching ownership receipt remains unowned. Identical unowned content can be a no-op; changed or unowned content is never adopted, overwritten or removed automatically.

An optional `.fenric-skill.json` receipt is local evidence with exactly `schema`, `name`, `scope`, `path`, `bundle_sha256` and `version`. The schema is `fenric.skill-ownership.v1`; scope is `global` or `project`; `path` is the exact scope-relative target such as `.claude/skills/fenric`. The digest binds the observed complete sorted member manifest excluding this receipt. This phase does not create receipts. A receipt copied to a different location, malformed or mismatching cannot establish intact ownership. Hashes establish content identity, not operator authorization. Extra files or empty directories, edited members, unsafe links, foreign ownership and hard links block changes. Independent copies in distinct roots of one scope remain independent targets. Matching aliases can remain no-ops, but mutating duplicate-alias or final-symlink proposals block; global/project overlap needs review. Do not use removal to clean an unrelated directory.

```bash
fenric lifecycle plan --scope global --target claude --remove
```

This only proposes removal of intact owned skills; it removes nothing. Missing targets are no-ops. Unknown, unreadable, invalid, missing, changed-during-read and conflicting states remain explicit. Limits and symlink policy are in the inventory. No truncated read receives a valid content digest.

## Later manual installation and recovery

Choose the actual owner: uv tool, pipx, staged venv or an explicitly identified unmanaged installation. Do not run all managers in sequence. Verify the intended artifact and dependencies, preserve the previous environment and settings, and allow current dialogs to settle before a separately authorized selection change. A CLI symlink switch does not update modules imported in a long-running process or retarget an absolute hook interpreter. Restart only a known owning service through its separately authorized lifecycle; standalone Fenric has no persistent service to invent or restart.

Keep rollback as the known previous artifact/environment and exact old selection. A partially completed or uncertain external operation needs readback before retry. This planner performs no installation, rollback, network setup, credential access or permission change. Existing explicit shared-instruction guidance commands remain separate operator choices; skills are the default, and constitutional files are not lifecycle targets.

The optional joint day-to-day workflow with another tool belongs to external operator-selected glue. It is not this installer skill or a dependency of Fenric. Native approvals still require their original supported callback; no lifecycle result changes that boundary.

## Reading results and verification

Inventory exit 0 means the selected observations completed, including reported conflicts or drift. Exit 4 means an observation was incomplete, such as unreadable or changing bytes, or a plan has blocked operations. Exit 3 means invalid input or an unavailable operation. Build the whole result before consuming it; do not infer success from a missing output or a bare exit code. Normalized observations, full member inventories and plan digests can be compared offline. No result proves cold-session discovery, live GUI behavior or native permission qualification.

Use fixture homes and skill directories for offline tests. Preserve constitutional bytes, modes and aliases, deny subprocess/network/write APIs during observation, and test deterministic repeated output plus changed-input invalidation. Live install, uninstall, restart, cold harness discovery and device acceptance are later authorized activities, not implied by passing these tests.
