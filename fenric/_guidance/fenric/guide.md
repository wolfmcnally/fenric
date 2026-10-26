# Working with people through Fenric

Use Fenric to collect the input the work actually needs, present a review, or show a useful result. It returns an answer to its caller; it does not send email, execute copied text, grant permissions or supply missing native callback custody. Continue already authorized work without needless approval requests.

## Choose the surface

| Need | Supported surface | Caller receives |
|---|---|---|
| One yes/no decision | `fenric confirm` with action-specific labels | JSON `true` or `false`; both exit 0 |
| One closed choice | `fenric pick` | Stable option ID |
| Free text, or a decision with comments | `fenric ask` or an ask spec | An object keyed by question IDs |
| Several substantial questions | Ask spec with `"presentation":"wizard"` | Active answers after final review |
| A report or acknowledgment | `fenric notify`; sanitized rich material via `custom` | Status only; acknowledgment is not action consent |
| Literal text to copy | Notify spec with `copy_text` | Status only; Copy itself does not settle the view |
| Pictures or screenshots with comments | `fenric ask --gallery-file gallery.json` | Comments under stable image IDs |
| Audio beside caller-prepared text or a score | Selected HTML plus exact WAV files, only on a supporting runtime | Presentation acknowledgment, not musical acceptance |
| Secret entry | `fenric secret` into an authorized supported destination | Status; never secret values in caller output |
| An original native permission | An already qualified owning adapter and supported callback | Only its admitted native response contract |

Inspect the receiving host's actual `fenric --help` and verb help before using an unfamiliar capability. Package installation does not configure phone access, install a skill into a harness or activate approval hooks. Read this guide with `fenric guidance read` and its optional skill entry point with `fenric guidance read --skill`; both are read-only and need no home-directory setup. Source documentation provides the complete command/spec and integration references.

## Ask one clear question

Give the user the consequence, material details and real choices. Keep acknowledgment separate from approval. For a yes/no decision, label the action instead of asking an unexplained “Proceed?”. This synthetic example collects a decision only:

```json
{"verb":"confirm","title":"Archive review","message":"Archive the reviewed report?","detail":"Move report.txt into the existing archive folder. No deletion or remote upload.","yes_label":"Archive report","no_label":"Leave unchanged","timeout":900}
```

Save a chosen spec as a caller-owned JSON file, then use the supported invocation:

```bash
fenric --spec-file review.json
```

The file must contain the exact spec being reviewed. This command is not authorization for its named action. Exit 0 plus `false` is a completed decline, not approval. A valid Fenric response does not override an independent requirement for authenticated operator provenance or a mandatory native security confirmation.

## Approve or decline with comments in the same view

Use one ask form with a closed decision field and a separate optional multiline comment. Both decisions can carry comments. Put the complete reviewed envelope, body and attachment list in `message`, not merely a mutable draft name or a link to content that can change. This public example sends nothing:

```json
{
  "verb":"ask",
  "title":"Review message",
  "message":"To: reader@example.org\nCc: none\nBcc: none\nSubject: Review summary\nAttachments: none\n\nHello,\nThe review is complete.\nThank you.",
  "presentation":"form",
  "timeout":900,
  "questions":[
    {"id":"decision","prompt":"Send this exact message?","kind":"choice","options":[{"id":"decline","label":"Decline / request changes"},{"id":"approve","label":"Approve unchanged message"}],"other":false,"required":true},
    {"id":"comments","prompt":"Comments, corrections or conditions (optional)","kind":"text","multiline":true,"required":false}
  ]
}
```

An answered result is an object such as `{"decision":"decline","comments":"Please revise the subject."}` or `{"decision":"approve","comments":"Thanks!"}`. An empty optional comment is `{"decision":"approve","comments":""}`; its key is still present. Treat whitespace-only comments as empty, not as a separate decision. **Never truth-test the object as a send gate:** a decline object is also truthy in many languages. Require the exact expected decision ID and valid comment type from the same invocation, then apply the caller's authority and current action-binding checks.

| Actual result | Caller treatment |
|---|---|
| Decline, with or without comments | Do not perform the action. Comments may guide a revision. |
| Approve, no comment | Eligible only for the unchanged bound action, within existing authority and deadline. |
| Approve, “Thanks!” | Plainly nonmaterial; may perform that same unchanged action under caller policy. Do not mechanically demand blank comments or another approval. |
| Approve, “Use a different subject” | Material edit: hold, revise the candidate and obtain any required fresh review. |
| Approve, “Only after the legal review” | Conditional: hold until the condition and authorization are reconciled. |
| Approve, “Do not send”, unclear instructions or contradictory comments | Hold and clarify the contradiction; the selected label does not erase the comment. |
| Wrong request/body, stale result, missing fields, cancellation, expiry or error | No action. Diagnose or report the actual blocker. |

These are caller judgment rules, not a Fenric comment classifier or an MCP requirement. Ordinary comments are a text field below the content, not anchored annotations or an editor that silently changes the displayed body. Do not treat user content as permission to execute arbitrary instructions or widen the original scope.

## Keep the decision bound to the work

Before showing a material action, retain its complete candidate and a caller-owned interaction ID, expected response shape and lifetime. For a message this includes recipient fields, subject, body and attachments; for another action include its consequential target, scope and effects. Use the same retained data to construct the view and later perform the action. A digest can help detect change but cannot authenticate a person or prove that a summary is complete.

Immediately before execution, verify the exact request/result correlation, unchanged material candidate, current authority and pending lifetime. An approval cannot float to another action, expanded scope, revised body or replacement invocation. If the action is still unchanged and authorized, do not ask again merely because internal preparation continued. If material details changed, obtain the required new review; never silently reinterpret an earlier approval.

Retain a durable operation ID or target-supported idempotency key when an effect may be retried. If delivery or execution acknowledgment is lost, reconcile the target's actual state before retrying. “Unknown” does not mean “nothing happened”, and another Fenric approval alone does not prevent a duplicate effect. This guide adds no transaction engine or automatic retry mechanism.

## One invocation, live link, one terminal result

Keep one caller-owned invocation alive while awaiting the answer. Do not start a second dialog for the same pending question, auto-select a response, or infer a choice from chat acknowledgment, URL delivery, a screenshot or a highlighted control. Respect the operator's availability and previously selected interaction workflow.

When phone access is already configured, collect early availability through `--remote-status-file` in a fresh private request directory or the library's `remote_status` callback. For example, with the JSON file and private parent directory already prepared:

```bash
fenric --remote-status-file review-status.json --spec-file review.json
```

The caller must track the running process; running this command and losing the process/result handle is not a complete workflow. Early status has `status`, `url` and `reason`; it has no answer or authoritative expiry. Retain the caller-owned start/deadline separately and promptly deliver the actual live protected URL, a minimal label and truthful remaining lifetime through an **already authorized, operator-selected** channel. Sensitive review contents need not accompany the link. This paragraph describes standalone delivery under the operator's chosen workflow. In request-bound integrated use, availability travels only through the already admitted host channel and protected relay described by the integration contract; a missing relay is unavailable, never a reason to copy the capability into a general message bus or ordinary output. A URL is a capability: do not place it in public output, general logs or an unapproved relay. If the chosen channel requires a delivery-time minimum, verify it at actual presentation, not merely when creating the URL.

The optional ordinary announcement command receives URL-only stdin; its success does not prove that a coordinator read the message or that the user saw it. A separately configured native-permission relay does not automatically announce ordinary questions. Fenric has no mandatory messaging product, coordinator identity or private-chat executable. Missing GUI, phone route or required handoff is a concrete blocker, not permission to install hooks, borrow an identity, widen networking or silently fall back to a worker-only question.

Collect the terminal stdout and exit status from **that same invocation**, or the corresponding library `Result`. CLI exit 0 means answered: inspect the exact JSON value or plain status. Exit 2 is cancelled, 3 invalid/error, and 4 unavailable or expired. A notification's `ok` is acknowledgment only. A nonzero status, malformed/missing result or unresolved transport failure grants no action authority.

An ordinary dialog's displayed inactivity countdown can stop after deliberate interaction; a native request's immutable authorization expiry is separate. Choose adequate reading/review time before starting, communicate its real limits and never extend or replay a settled authorization. Do not infer expiry solely from a clock displayed in a screenshot.

When the invocation terminates, collect its result and allow its own resources to close. Cancel only resources you own using their supported lifecycle. Another dialog may occupy a shared proxy mapping or port after yours ends; do not clear all Serve mappings, kill unrelated windows or claim global cleanup from your process disappearing.

## Copy is inert text, not approval or execution

Use `copy_text` for text the user should paste, with the surrounding explanation in `message`. Do not put Markdown fences into the payload unless they are intended literal text. This example includes straight quotes, a backtick, Unicode, blank lines and a trailing newline:

```json
{"verb":"notify","title":"Copy sample text","message":"Copy this inert sample into your notes. It performs no operation.","copy_text":"A \"quoted\" note with `backticks` and café.\n\nLast line.\n","timeout":900}
```

Fenric passes only the supplied text to the clipboard API, without application trimming, quote substitution, wrappers or execution. Browsers and operating systems may normalize line endings; do not promise universal byte-identical clipboard contents across platforms. Test leading/trailing whitespace and the terminal newline separately from pasting into an ask field: single-line questions cannot contain line breaks, while multiline text questions preserve them. Returned ordinary text answers retain outer whitespace; trimming is used for blank validation and the choice “other” field, not to rewrite ordinary answers. Copy success feedback is not proof of the pasted destination's contents. A clipboard failure is shown with selectable text for manual copying, and never reported as success. Copy does not acknowledge the dialog; the user can still dismiss it separately. Do not inspect the user's clipboard to claim success.

## Secrets and native approvals stay separate

Collect secrets only through the existing direct-destination secret surface, after verifying the authorized receiving host and destination. Never place values in ordinary questions, comments, chat, command arguments or logs, and do not inspect typed values or read them back. Use the separately authorized channel appropriate for a secret-entry link; do not assume permission to relay it from an ordinary-question workflow.

Standalone answers validate result shape, not authenticated operator origin. When the caller requires authoritative principal evidence, use an already admitted host-owned private response channel and trusted renderer; consult that installed integration's exact policy. A URL, forwarded answer, digest or coordinator signature alone supplies no such evidence. Authentication of an account is not proof of a particular device or person.

Settle an original native permission only through its qualified owning adapter and supported callback. A generic `ask` cannot acquire a callback from an existing native prompt, reverse an auto-mode/classifier denial, grant new permissions or bypass sandbox/network rules. `fenric interact --channel-fd N` consumes an already admitted inherited channel; the flag creates no authority. Report missing custody or qualification instead of inventing a bridge or mislabeling a standalone answer as a native approval.

## Report the state that actually occurred

Use **created**, **URL available**, **submitted**, **caller accepted**, **queued**, **executed** and **verified effect** only when each fact is observed. A submitted approval is not proof that an email was sent. A queued job is not execution; a lost acknowledgment leaves its outcome unknown. Explain a blocker and the smallest supported next step without claiming work is active merely because it was requested.

Keep descriptions readable, controls labeled and important material visible on mobile. Prefer an explicit wizard for several substantial decisions; no question count switches modes automatically. Preserve keyboard accessibility after deliberate engagement and do not raise another app or steal focus to demonstrate an interaction. Guidance and component tests are not an accessibility-conformance certification or exhaustive device QA.

## Research basis and limits

Retrieved 2026-10-22. These sources inform recommendations; the actual installed Fenric schema remains the API contract. No MCP, WCAG or other standards-conformance claim is made.

- [Agent Skills specification](https://agentskills.io/specification): current format and progressive disclosure (undated live specification).
- [Anthropic: writing effective tools for agents](https://www.anthropic.com/research/writing-tools-for-agents), published 2025-09-11: concrete descriptions and task-based evaluation.
- [MCP elicitation, 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation): distinguishes acceptance, decline and cancellation; it does not prescribe this guide's comment interpretation.
- [GOV.UK check answers](https://design-system.service.gov.uk/patterns/check-answers/), undated live pattern: understandable review and action-specific labels.
- [OWASP Transaction Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Transaction_Authorization_Cheat_Sheet.html), undated live guidance: significant transaction details and final execution checks.
- [W3C Clipboard API working draft, 2026-06-24](https://www.w3.org/TR/2026/WD-clipboard-apis-20260624/): platform-specific clipboard processing, including newline normalization; a draft, not a finished standard.
- [WAI dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/) and [timing guidance](https://www.w3.org/WAI/WCAG22/Understanding/timing-adjustable.html), undated live guidance: deliberate interaction, readable content and adequate time. Do not assume every security timeout qualifies for an accessibility exception.

Before running a media invocation, create a fresh owner-only (0700) request directory outside the checkout and set `STATUS_DIR` to its absolute path. Put status files there: they can contain live bearer URLs. Never reuse a status file, symlink or public directory.

To check WAV selection without opening a window or starting remote delivery, create another fresh private directory, set `CHECK_DIR` to its absolute path, and put a minimal `index.html` and empty `probe.wav` inside it. Run `FENRIC_HEADLESS=1 fenric html --root "$CHECK_DIR" --entry index.html --file index.html --file probe.wav`. Exit 4 with exactly `unavailable: headless` means this runtime accepted WAV selection; exit 3 means refusal or invalid input (including unsupported WAV on published 0.2.0). Any other result is unknown: inspect it, do not assume support. This checks selection only, not audio validity, GUI readiness or decoding; it does not create a headless-only presentation route.

## Screenshot and picture galleries

Use the gallery surface for pictures front and center, with optional per-image comments and final review. Give every image a stable ID, a useful name and an honest caption. Preserve simulator-versus-physical capture provenance and visible defects. Prepare this manifest beside two caller-selected static PNGs:

```json
{"title":"Screenshot review","message":"Feedback only; submission approves no other action.","timeout":3600,"images":[{"id":"portrait","name":"Portrait layout","file":"portrait.png","caption":"Caller-provided portrait capture."},{"id":"wide","name":"Wide layout","file":"wide.png","caption":"Caller-provided wide capture."}]}
```

Run `fenric ask --gallery-file gallery.json --remote-status-file "$STATUS_DIR/gallery-status.json"`, retaining the process and a fresh private status path. Actual terminal JSON is keyed by image ID, for example `{"portrait":"Enlarge the label.","wide":""}`. Comments remain drafts until final Submit. Back/Next and review/Edit preserve them within the open page; reload, cancellation and expiry discard unsubmitted drafts. Natural size inspection does not remove the hard lifetime.

Bounds: 10 static PNGs, 2 MiB of PNG file bytes each / 8 MiB combined, 8192 pixels per side, 16 million pixels each / 24 million combined; 64 KiB manifest and 1000 UTF-16 units per comment. No SVG, animated PNG or network image URLs. Make a separate aspect-preserving PNG copy if needed, retain originals and hashes, and inspect size/readability. Do not crop or retouch defects. A modest maximum side such as 1100 pixels is a starting point, not a byte-size guarantee. Label resizing and original provenance. `required:false` is optional; blank optional comments return an empty string.

Promptly deliver the real protected URL and caller-owned remaining lifetime through the existing authorized channel, then collect this same invocation's terminal result and owned cleanup. Do not park before handing off the link or create a duplicate gallery. Missing GUI/private delivery is a blocker, not permission to change access. If handoff fails, report it and keep the same invocation through normal termination; use only its supported cancel path if ending early, never clear shared routes or orphan it. See **One invocation, live link, one terminal result** above and the source documentation's `docs/media.md` and `docs/examples.md#comment-on-screenshots` for complete examples.

## Selected WAV audio

Selected-WAV audio requires Fenric 0.3.0 or later; published 0.2.0 does not accept WAV bundles. Development builds may share an older version string; verify the receiving runtime's actual selected-file contract or exact source/package provenance when uncertain. Never silently install or replace it to satisfy a listening request.

On a supporting runtime, prepare HTML with escaped plain text in `<pre>` beside native `<audio controls preload="metadata" src="preview.wav"></audio>`. Select exactly the HTML and its paired WAV using `fenric html --root listening-preview --entry index.html --file index.html --file preview.wav --timeout 900 --remote-status-file "$STATUS_DIR/audio-status.json"`; all paths refer to caller-prepared files. There is no audio verb, music compiler, transcoder or new hosting service. Escape score text as text, never execute it or insert it into script. Retain exact text/audio association and immutable hashes; do not silently substitute a preliminary recording or truncate a long one.

Only WAV is added, served as `audio/wav`; the public demo exercises uncompressed PCM16. Other WAV encodings depend on the receiving browser and require their own decode/playback check; other formats/codecs are not promised. At most eight WAVs with 1024 bytes of combined percent-encoded relative WAV names, within existing 16 MiB/file, 128 MiB total and 4096-file selected-bundle bounds. Only exact selected WAV URLs receive media permission; external/data/blob media remain blocked. Single-byte-range delivery supports seeking over immutable snapshots with existing owner/expiry checks. User-controlled Play/Pause/seek/replay neither settles the view nor extends its deadline. Playback does not count as interaction, so the untouched countdown continues; set the timeout above recording length plus reading time. Do not autoplay or read the user's clipboard. Observable playback is not speaker audibility, musical judgment or action consent.

Collect availability and promptly relay its actual protected link and caller-owned lifetime before waiting; retain the same invocation through result and owned cleanup. Respect unavailable/cancel/expiry without automatic replay. The source archive's `docs/examples/audio-demo/` contains a tiny original MIT chime with a generator and provenance, not a private recording; it is absent from the wheel. The full source recipe is `docs/media.md`. Keep private/large recordings out of repository and release inputs. Do not widen networking, credentials, authentication or permissions to make playback work.

## Unchanged selected HTML reports

Use the `html` verb for an existing static HTML report that needs its own relative navigation, scripts, styles or data. Supply `root`, HTML `entry` and the exact `files` allowlist in a spec; repeat `--file` for a small CLI selection. Files are snapshotted byte-for-byte before display; no recursive directory serving or content transformation occurs. Include only caller-reviewed presentation files, never secrets, raw provider transcripts or build inputs merely because an index links them. Unselected resources refuse. Use `custom` for sanitized inert markup and `chart` for compiled DOM/d3 instead.

The generated frame is opaque and restricted to selected resources; no parent/native authority, external network, form submission, popup, worker or nested frame is added. Inline scripts/styles and data/blob images/fonts are supported. Embedded activity does not extend the deadline or count as outer touch. Choose an explicit timeout up to 3600 for long reviews, deliver the actual protected URL promptly through the operator's authorized channel, and retain that same invocation through its terminal result and cleanup. OK acknowledges presentation only; it does not approve embedded actions or prove every report page was read. See the command and spec references for exact bounds and supported file types.
