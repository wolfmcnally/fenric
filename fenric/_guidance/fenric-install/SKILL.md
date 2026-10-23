---
name: fenric-install
description: Inspect a Fenric installation, skill ownership and scope conflicts, and prepare a read-only lifecycle plan before an operator-managed install, update or removal.
---

# Fenric installation planning

Read the [installer guide](guide.md). Use `fenric lifecycle inventory` and `fenric lifecycle plan` to inspect the receiving host and review proposed changes. Both commands are read-only; they do not install, update, remove or authorize anything.

Select the installation owner and skill scope explicitly. Preserve user edits, existing runtime selection and rollback. Runtime package version, skill content identity and a harness's actually loaded guidance are separate facts. Unknown ownership or conflicting scope needs review; an identical unowned skill stays unowned.

Use the ordinary `fenric` interaction skill for questions and reviews. This skill needs no messaging product, joint workflow skill, credentials, global instruction edits or native approval setup. Keep `CLAUDE.md` and `AGENTS.md` untouched by default. Do not turn missing inventory evidence into an install attempt or a native test launch.
