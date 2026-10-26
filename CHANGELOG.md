# Changelog

All notable changes to Fenric are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/). Until 1.0, a minor version may change the spec or the command line.

## [0.3.0] — 2026-10-26

### Added

- Selected WAV playback beside caller-prepared text or scores in the existing private selected-HTML surface. Exact selected-file media policies and immutable byte-range delivery support native browser Play, pause and seeking without granting external media access, filesystem authority or a new public route. Existing expiry and file-size limits remain; browser decoding and speaker audibility are separate from transport success.
- Read-only lifecycle inventory and deterministic versioned dry-run plans, with separate runtime, skill-content and harness-loading evidence. Passive packaged interaction and installer skills are the default setup route; no apply endpoint, constitutional-file edit, grant or runtime installation is performed by lifecycle inspection.
- Prominent picture-gallery and audio-preview recipes in user docs and packaged agent guidance, including proportional PNG sizing, original/copy provenance, a dialog-free WAV-selection probe, prompt authorized protected-link handoff and same-invocation result/cleanup ownership.
- A reproducible original MIT synthetic chime, 0.6 seconds and 9644 bytes, for the source-documentation audio example. The source archive includes only this named tiny demo audio; the wheel includes no audio. Packaging tests exclude private/large local media and execution evidence.

### Fixed

- Test-only private-transport teardown accounts for a listener disappearing after the route's removal check, preserving failure for unrelated proxy errors and the original response assertions.

## [0.2.0] — 2026-10-23

### Added

- Unchanged selected-file HTML reviews with relative navigation and explicitly selected presentation assets, bounded byte snapshots and an opaque frame on desktop and configured phone surfaces. No arbitrary directory serving or external network access.
- Resizable native selected-HTML windows: one initial fit within the usable display, followed by person-controlled sizing with an adapting scrollable content frame and separate trusted controls. Other dialog sizing, focus safety and deadlines remain unchanged.

### Fixed

- Stop remote state polling after three consecutive failed exchanges, reset the count after successful exchanges and clear stale response controls with an explicit unconfirmed-response message. Normal expiry and one-use settlement remain unchanged.
- Keep remote configuration diagnostics out of captured documentation examples while preserving real runtime diagnostics.
- Exclude local review and execution evidence from distribution archives, with a synthetic-marker packaging regression that checks both wheel and source archive.

## [0.1.0] — 2026-10-22

First public release.

### Added

- Packaged agent interaction recipes and an optional skill entry point, discoverable through read-only `fenric guidance read`, CLI help and public documentation. Covers explicit approve/decline with comments, authorized link handoff and distinct answer/action outcomes; configures no hooks or permissions.
- The `fenric` command and `fenric.run` Python library function: one spec in, one native dialog, one validated result or status out. Exit statuses distinguish answered (0), cancelled (2), invalid (3) and unavailable (4).
- Eight verbs: `notify`, `confirm`, `pick`, `ask`, `secret`, `form`, `custom` and `chart`, with strict input/result validation and bounded lifetimes.
- macOS windows that preserve the active application's keyboard focus, keep resized dialogs inside usable display bounds and require deliberate engagement before Space/Enter can respond. Oversized content scrolls.
- Multiline ask/form answers that grow and shrink with wrapped text up to ten visible lines before scrolling. Literal UTF-8 notification payloads with a dedicated Copy button preserve whitespace and quotes; clipboard failure is visible and copying does not acknowledge the dialog.
- Explicit ask wizards with branching, Back/Next, retained active answers, final review and Edit. Hidden branch answers are cleared and omitted. Ordinary ask/form layouts remain available.
- Paged local PNG galleries with stable image/comment IDs, fit/natural-size inspection and final review.
- Operator-configured temporary tailnet HTTPS dialogs with authenticated account checks, shared desktop/phone first settlement, early availability status and optional notification. No public route or headless-only shortcut.
- Countdown feedback and immutable hard deadlines. Only deliberate control interaction or answer edits cancel the displayed countdown; blank taps, scrolling and focus alone leave it running.
- Direct secret destinations: a store command receiving each value on stdin, or an authorized dotenv/direnv, flat JSON or single-value file. Receiving-host and destination disclosure precede submission; values stay out of ordinary output.
- Sanitized custom HTML in an inert frame, and caller-supplied DOM/d3 charts with ordinary authorized browser networking. Charts require a separately provisioned locked Bun toolchain; bundled third-party license notices are included.
- Reviewed shared agent guidance through `fenric guidance preview`, `apply` and `check`, with backups, preserved user edits/symlinks and explicit read-path gaps. Setup installs no native approval hook, permission or authentication change.
- Optional request-bound integration on an independently admitted private Unix channel, with complete original action/context, actual surface observations, immutable expiry and distinct response, receipt, native delivery and effect reporting.
- Exact supported Codex command and MCP confirmation mappings with original RPC/action retention and only actually offered one-action choices. Persistent/session/rule alternatives and unsupported authority refuse; live native ownership and delivery require separate qualification.
- An opt-in manual-mode Claude Bash PermissionRequest helper retaining the original callback, exact input, account and one-use request. It requires independently approved host configuration and relay; it cannot reverse auto-mode classifier denial or capture unrelated existing prompts.
- Worked CLI/library examples, command/spec/security references, package inventory checks, screenshot documentation and installation/update/rollback guidance. macOS is tested; native Linux/Windows GUI behavior remains unverified.
