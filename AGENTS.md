# Agent instructions

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing or delivering work, and preserve the [security model](docs/security.md).

Agents commit and push approved, scope-owned work to the existing authorized upstream without a separate human approval once the applicable review and repository checks pass. Follow CONTRIBUTING.md for validation, custody, explicit staging, hooks, commit verification and actual remote readback. Human demos, product taste and subjective acceptance are separate from qualified software delivery; report those pending checks without treating them as a delivery gate.

Check for another writer and unexpected changes before mutating shared state. Stage only attributable paths and keep hooks enabled. Stop on uncertain ownership, failed gates, hook refusals, ambiguous upstream or divergence; diagnose within existing authority without bypassing protections.

Preserve explicit task-specific local-only, no-publication and deployment holds. This authority does not create or select a remote, expand publication scope, authorize force pushes or history rewriting, create a release, change installation, credentials, access or security settings, or bypass platform permissions or required action-time confirmations.
