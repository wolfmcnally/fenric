# Synchronous native permission host

Available in development version 0.4.0.dev0; published 0.3.0 does not contain this adapter. Code/package deployment is distinct from hook registration and live acceptance.

The opt-in `fenric.native_permission` module handles **fresh default-mode Claude Bash PermissionRequest** events synchronously. It installs no hook. It is separate from the older phone-only `fenric.claude_permission` helper and does not change that helper's deployed configuration. A standalone Fenric answer, peer response, delivery acknowledgment or signed notification is never a native permission decision.

The original harness must invoke the module on its pending request, retain its callback, and consume the single JSON decision on stdout before that invocation returns. No command input, rule, permission mode or future allowance is changed. Unsupported events or modes, invalid configuration, failed delivery, cancellation, expiry and errors return explicit JSON `deny`; exit 2 alone does not deny a PermissionRequest. Dialog lifetime is 540 seconds, earlier than the documented 600-second default synchronous hook timeout. Background/async registration is unsupported.

## Host-owned policy and routing

An owner-controlled regular profile file must be owned by the process account and readable only by that account (mode 0600). It pins receiving hostname, UID, project, exact cwd, optional native session, participant when present and a principal-policy reference. It is reread before consuming an allow; drift refuses. These are host policy inputs, not agent, coordinator or browser assertions.

A minimal **unactivated template**, not a ready-to-use profile, is:

```json
{"schema":"fenric.native-permission.v1","host":"RECEIVING_HOSTNAME","uid":501,"project":"sample","cwd":"/example/project","session_id":null,"participant":null,"principal_policy":"owner-local-policy-v1","folded_present":false,"coordinator":null,"expected_remote_login":null}
```

Use the qualified environment's absolute Python with `-I -m fenric.native_permission OWNER_PROFILE.json` as a synchronous command hook. With no Folded integration in the owner-selected profile, the local native dialog needs no Folded dependency or remote startup. Local admission records the actual process UID: account evidence, not a physical person or device. The profile must explicitly admit that account. No signing identity is fabricated when the participant is absent.

With Folded integration, absent/invalid assignment refuses instead of silently selecting an unassigned peer or falling back locally. The profile must pin the actual native session and participant, expected server-verified Tailscale login and this coordinator object:

```json
{"fingerprint":"DELEGATE_FINGERPRINT","folded":"/absolute/qualified/folded","designation":"EXACT_PUBLICATION_ID","nexus":"EXPECTED_NEXUS_FINGERPRINT","scope":{"launcher":"LAUNCHER_FINGERPRINT","owner":"ORIGINAL_OWNER_FINGERPRINT","project":"sample","harness":"claude","operations":["coordinate"]}}
```

This template grants no designation. The existing `folded coordinator verify` checks the current exact delegate/publication/launcher/original-owner/project/harness/operation, current standing, expiry, revocation and replacement. Fenric compares the returned scope/nexus/delegate/publication, repeats verification before relay and consumption, and binds native host/session/transport generation independently in the caller. The designation does **not** prove receiving-session custody or native UUID/host/generation fields it lacks.

Delivery uses `folded send --current-session`, exact intended recipient and original native session ancestry. Only the protected URL, request ID and expiry metadata are delivered; action contents and the operator response do not traverse the coordinator. Command output is discarded, never an answer. Commands have bounded timeouts; verification/delivery failure refuses without retry. Queue admission is distinct from user reading.

Installation creates no live designation or qualified host profile. Confirm the concrete assignment and exact host/scope before coordinator activation; membership or a peer assertion is insufficient. General embeddings can use `fenric.host.Host.collect` with an already admitted request and `Coordinator`; they still own native callback custody and current host policy. Collection returns the authenticated bound result directly to the caller and executes no native action.

## Binding and activation

The host retains complete original action and optional-field presence, native session, policy, immutable expiry and exact digest. Each invocation gets a fresh private socket pair and transport generation. Existing renderer admission checks the configured actual UID or exact server-verified remote login. Consumed requests, wrong action/session/principal/generation, stale responses and unavailable results refuse. PermissionRequest supplies no tool_use_id: the dedicated `claude.permission` mapping keeps it null. Other mappings retain their actual identifier and participant requirements.

Before persistent activation, prepare the exact current settings diff and protected profile with the owner. A suitable command hook synchronously matches Bash on PermissionRequest, uses `timeout:600` and `onFailure:"block"` (Claude Code 2.1.295+). Confirm runtime/profile paths and hook ordering on each receiving host, preserve unrelated hooks/rules/identity/settings, retain drift-checked rollback and avoid active permission dialogs. Package deployment activates nothing. Test only a fresh native transaction after supported activation, never replay a denied action.

## Auto-mode boundary

As of 2026-10-28 UTC (retrieved the same day), the [official hooks reference](https://code.claude.com/docs/en/hooks#annotate-a-result-for-the-auto-mode-classifier) distinguishes configured notes from live in-process Agent SDK annotations. Configured notes cannot establish user intent; an already owning live SDK integration is required for the stronger statement, and restored/mixed notes lose that trust. A plain daemon-managed CLI has no SDK callback owned by this module. This handler cannot reverse its classifier denial or acquire that callback; `PermissionDenied` retry is not an override.

A future SDK-owned integration needs its own live query/callback admission and proof. No existing-session identity migration, new host framework, blanket allow rule or mode downgrade is included. See [integration](integration.md) for channel/principal semantics. Automated tests do not prove live workflow acceptance or physical-person authentication.
