# Troubleshooting

Start by identifying the receiving host and resolving its actual fenric executable. Inspect --help and the relevant verb's --help; do not assume another machine's installation, GUI, chart toolchain or remote configuration. Protect diagnostics: no secret values, destination contents, private capabilities or credentials in chat/logs. The [command reference](commands.md) lists fixed errors and all exit statuses.

| Symptom | Meaning and supported next step |
|---|---|
| Command missing or an expected verb absent | Check PATH/actual installation owner and installed help. A version string alone cannot distinguish same-version builds. Use the reviewed installation procedure; do not create new symlinks automatically. |
| Exit 3 / invalid invocation or spec | Check the exact verb/options against help and the spec reference. Unknown fields and unsupported mappings refuse before a window. Correct the input; do not reinterpret an error as a negative or positive answer. |
| Exit 4 / headless | A working native GUI is required. Headless refuses before remote startup. Use a supported GUI host, not a remote-only bypass. |
| Exit 4 / render failure | Check that the native desktop session and pywebview backend work on this host. A failed native renderer also ends remote availability. Do not assume a live URL survived. |
| Exit 4 / timed out | The interaction ended without an answer. Preserve the expired result. A newly authorized attempt needs a fresh interaction/token/deadline, never extension or replay. |
| Exit 2 / cancelled | No affirmative answer. Selected controls or screenshots are not submitted results. A local close and an authenticated phone Cancel are distinct observations. |
| Exit 0 with false | A valid No answer, not an error. Only True for the exact question is an affirmative standalone value; authoritative native use additionally needs trusted-channel/callback checks. |
| Remote unavailable | Check remote status privately and the [complete fixed reason list](commands.md#remote-access). Existing host setup is required. No caller spec can enable it. Do not add Funnel, grants, change login or reset unrelated Serve mappings as a workaround. |
| Phone URL disappears | Settlement, expiry, native render failure, cancellation or parent death closes its owned route. An early URL is availability only. Do not automatically retry a possibly completed action. |
| Chart refused before UI | Chart code/type errors or missing preprovisioned toolchain are invalid requests. Inspect code without exposing compiler/private values. Provisioning tools/network requires explicit operator authorization. Other verbs need no chart toolchain. |
| Gallery refused | Use local bounded regular PNGs and the documented manifest limits. Files resolve relative to the manifest; invalid input must not be replaced with silently dropped images. |
| Secret destination refused or write uncertain | Verify authorized non-secret destination metadata/format without reading secret contents. Blank fields are skipped; ok does not prove every field was supplied. Partial/unknown effects are not automatically retried. |
| Guidance check incomplete | Read per-harness missing/broken/conflicting results. Preview/apply only known owned blocks. Preserve user edits; do not create/retarget read paths or alter permissions merely to get green. |
| Native approval remains pending | A generic Fenric answer or coordinator signature does not resolve harness policy. The host needs exact supported original callback custody and current admission. Report the missing bridge/policy capability rather than bypassing it. |
| Response has unknown delivery/effect | Keep that disposition. Receipt, callback submission and actual effect differ; inspect non-secret correlated native evidence before any fresh action. No blind retry after ambiguous send/write. |

## Remote and authentication checks

A Tailscale login authenticates an account, not a specific phone or physical person. URL possession, forwarded prose and browser-provided principal fields are insufficient. The integrated host compares the complete observed principal tuple and binding against its current admission, at consumption as well as presentation. Do not log a status file containing a capability while diagnosing remote readiness. See [security](security.md) and [integration](integration.md).

## Lifecycle and restart

A package or settings change may leave a long-running host on old imported code. Preserve pending-request status, identity/session generation and rollback before coordinated quiescence. Restart through supported lifecycle only; do not kill unknown owner windows. Lost native callback custody means unavailable/unknown, not a token to replay. Automatic service startup, receiver-observed queued messaging and native request recovery require actual cold-start testing; a ready registry row alone is insufficient.

## Getting help

Report host/platform, resolved executable, package/artifact version identity, fixed exit/status/detail and the non-secret operation being attempted. Include whether the problem is standalone collection, remote availability, authenticated receipt, native submission or effect. Exclude typed secrets, key files, settings contents and live URLs. There is no promise that changing permissions resolves an unqualified integration route. See [installation recovery](install.md) and [supported native boundaries](native-approvals.md).
