# Standard agent-guidance setup

For usage rather than instruction installation, start with `fenric guidance read` and the [agent interaction guide](../fenric/_guidance/fenric/guide.md). `fenric guidance read --skill` prints the optional [skill entry point](../fenric/_guidance/fenric/SKILL.md). The package includes both files, but does not automatically register a harness skill. If the operator chooses skill installation, retain the whole `fenric` skill directory, including its guide reference, using that harness's supported procedure. Its source-tree location is `fenric/_guidance/fenric/`, also a resource directory inside the installed `fenric` package; printing the entry point alone does not copy its referenced guide. This is separate from the shared-instruction setup below.

The instruction template is the concise always-loaded policy; the guide owns the detailed recipes. This version updates the template's pointer to the installed guide. Existing managed blocks may report `update-required` until the operator reviews a fresh preview and applies it. Installing the package or reading the guide never updates live instruction files.

After installing Fenric, review its agent-guidance setup on each receiving host. This is a standard installation step, separate from enabling phone access or configuring a native approval adapter. It writes prose only when the operator explicitly applies an unchanged preview. It never installs hooks, grants permissions, edits MCP allow-lists/authentication, or creates/retargets a harness instruction file.

Use `fenric guidance preview --plan guidance-review.json` to inspect the two conventional instruction paths beneath the selected home: `.claude/CLAUDE.md` and `.codex/AGENTS.md`. `--home` selects another existing home explicitly. The JSON plan shows each expected read path, its resolved file, symlink identity, current content hash and the exact proposed guidance diff. It is owner-readable only and created exclusively; use a new plan filename for a changed preview. Missing, broken, unreadable, hard-linked or conflicting paths are explicit. This measures filesystem coverage, not whether a running harness loaded the file; custom harness homes, overrides and alternative instruction configuration require separate verification. Never infer Codex coverage from a Claude file alone.

Read the proposed diff before running `fenric guidance apply --plan guidance-review.json --backup-dir guidance-backup`. The backup directory must be new. Apply recomputes the plan from this home and the packaged template, refuses changed content/identities or a changed template, backs up all changing files privately, then atomically replaces each resolved target. Shared symlink aliases are updated once while their links remain intact; distinct files are updated independently. File permissions and unrelated bytes are preserved. Do not edit the files concurrently with application: this is a cooperative setup operation, not isolation from another process under the same account.

Use `fenric guidance check` afterward. It reports each harness separately and always reports native adapter readiness as `unverified`. The setup template expresses the all-response preference, actual response trust checks and supported fallback; its presence does not authenticate an answer or qualify a native approval route. Native permissions can resume only through the original supported bridge/callback, with exact pending action and authenticated principal binding. See [request-bound integration](integration.md) for account-evidence limits, one-use/expiry checks and collection-versus-delivery/effect distinctions.

## Operator-selected question routing

Machine-level interaction guidance belongs to Fenric's installation on the receiving host. It does not require edits to every project or create a dependency from a project template or messaging product onto Fenric. Standalone dialogs remain usable without a coordinator. If the operator selects a coordinator and an authorized delivery channel, agents use that host-level workflow for their own questions, choices and action confirmations, including when an older project skill names a worker-only question tool. The packaged guidance describes the generic contract; it does not choose a recipient, discover credentials, configure a transport or enable any native hook.

Retain the complete proposal, expected choices and a caller-owned lifetime bound before starting one tracked invocation. A closed choice can use `fenric pick`; free text can use `fenric ask`; several substantial questions can use an explicit `ask` wizard. Add `--remote-status-file` with a fresh filename in a private request directory whose parent already exists, or use the library's `remote_status` callback. Keep that invocation alive and promptly relay its live protected URL with only the necessary question label and lifetime information through the caller's existing authorized channel to the selected coordinator. The coordinator presents it in the operator's chosen conversation even if the local window opened. Do not forward sensitive draft contents just to deliver a link, and do not create a duplicate dialog.

The early status object contains only `status`, `url` and `reason`; it is availability, not a response or an authoritative expiry/principal receipt. A missing file, unavailable status or missing delivery channel is a blocker to report to the configured coordinator, not permission to silently use a worker-only native question. Ordinary `ask` and `pick` do not automatically use a separately installed Bash-permission relay. The configured announcement command, if any, receives URL-only stdin; enabling a new command or borrowing another process's identity is outside guidance setup. See [remote status and announcements](commands.md#remote-access).

Only the originating caller consumes the terminal result from the same invocation and compares its exact value with the unchanged proposal and current scope. Exit 0 means answered, not necessarily approved: a selected decline or Hold is an answer that authorizes no action. Exit 2 is cancellation, 3 invalid/error and 4 unavailable or expired; missing/crashed/uncertain output grants nothing. A link, chat acknowledgment or forwarded answer does not replace the terminal result. Keep collection, any native delivery and the actual action result separate. Standalone output does not expose the winning surface or principal attestation; a receiver requiring that evidence needs an already admitted [request-bound integration](integration.md). Mandatory native security prompts and prior denials remain in force. Diagnose each failed handoff instead of claiming successful delivery or extending a stale request.

On a machine with existing custom Fenric prose, compare the operator-approved routing text with the actual canonical file and both harness read paths. Reconcile only the explicitly authorized paragraphs, preserving unrelated bytes, file modes, symlinks and a protected before-copy; verify the exact diff and readback. Do not automatically adopt, delete or overwrite an unmanaged section merely to satisfy the installer. File coverage and a session's actual loaded instructions are separate facts, and one host's coverage proves nothing about another. Applying the new packaged template to a managed block still requires its normal unchanged preview and explicit apply; installing a newer package alone does not update host instructions.

## Existing instructions and missing read paths

The installer owns only its unique begin/end-marked, body-hashed block. Repeated preview/apply with unchanged guidance changes no bytes. A later package update replaces only an intact owned block. Duplicate/malformed markers, changed owned content, or a separate heading beginning with “Fenric” stop application for review. A bare mention of the command or a checkout path is not a trigger. A heading is only a conservative lead to potentially conflicting guidance, never proof that arbitrary prose is equivalent or owned. Custom existing prose must be reconciled explicitly by the operator before installation; it is never automatically deleted, adopted or duplicated. The manually updated global guidance on a particular machine may therefore be present while automated ownership remains unverified.

A missing Codex path is a configuration gap even when Claude guidance exists. Check the actual harness's current instruction configuration, decide which file it should read and obtain authorization for any file/symlink creation. This command makes no such change. After that separate setup, rerun preview/check on that host. A file's presence, and even a correct symlink, is filesystem evidence; verify actual harness loading separately. No settings or configuration is inspected for credentials.

## Removal and recovery

Preview removal with `fenric guidance preview --remove --plan guidance-removal.json`, review it, then apply that plan with a new backup directory. Removal touches only an intact owned block; repeated removal is a no-op. Modified or unowned guidance remains untouched for operator review.

Every changing target has original bytes in the private backup directory and a manifest binding target and digest. An `outcome.json` distinguishes completed and unproved/pending targets on partial failure. Per-file replacement is atomic, but a multi-file setup is not a transaction. An error may follow a completed replacement: inspect readback and backups before deciding what to do; do not blindly replay or promise rollback. Recovery is operator-owned: compare the current file and the specific saved original, preserve any newer edits, and explicitly restore only the intended content while keeping instruction symlinks. The tool performs no destructive automatic restore.

## Help

```bash
fenric guidance check --help
```

<!-- capture {} -->
```text
usage: fenric guidance check [-h] [--home HOME]

options:
  -h, --help   show this help message and exit
  --home HOME  home whose existing instruction paths are selected
```

Exit 0 means the requested guidance operation completed or a check found current managed guidance on both expected read paths; exit 4 means incomplete coverage/review needed; exit 3 means an invalid/unavailable operation. None of these statuses proves a native adapter operational. Ordinary dialog status meanings remain unchanged.
