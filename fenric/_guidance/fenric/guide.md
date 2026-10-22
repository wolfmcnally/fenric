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
