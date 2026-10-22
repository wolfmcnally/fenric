# Python API and tutorial

Fenric is independently installable and usable from Python. It requires Python 3.11 or later and a working native GUI for presentation; importing the package does not open a window. See [installation](install.md), [worked CLI examples](examples.md) and the [spec reference](spec.md). The public exports below are the supported package entrypoints; internal renderer, protocol and destination helpers are not an authority factory.

## Public exports

| Export | Purpose |
|---|---|
| `run` | Validate one ordinary spec, present it and return a Result. |
| `Result` | Immutable ordinary collection result. |
| `load_gallery` | Read a bounded local PNG manifest into a validated ask spec. |
| `run_integrated` | Collect one host-admitted request on a connected private Unix stream socket. |
| `IntegratedResult` | Separate collection, host receipt, native disposition and effect reports. |
| `__version__` | Installed distribution version string; use artifact hashes to distinguish builds with the same version. |

## run

Signature (the internal interaction argument is deliberately omitted):

```text
run(spec, *, backend=None, fallback_file=None, desktop_only=False, remote_status=None, status_file=None, cancel=None) -> Result
```

| Parameter | Contract |
|---|---|
| `spec` | An ordinary specification mapping; fields and result shapes are in the spec reference. Invalid specs return error before rendering. |
| `backend` | Trusted renderer/testing override implementing show(page, session). Omit for the native window. A scripted backend can synthesize an answer; it cannot prove human consent. |
| `fallback_file` | Optional path for a plain-text request when unavailable; never a secret-value fallback. The caller chooses and protects this destination. |
| `desktop_only` | True disables remote for this call. False does not enable unconfigured remote access. |
| `remote_status` | Optional callback receiving early status/url/reason. Availability is not an answer. Treat a URL as a protected capability; never log it by default. |
| `status_file` | Optional early remote-status destination, not an answer file; protect it like the URL it may contain. |
| `cancel` | Optional threading.Event; setting it cancels the pending interaction. It does not undo a completed sink. |

A valid request may compile a chart or prepare a secret destination before showing a window. Ordinary error results are value-free. An exception from backend.show is converted to unavailable with detail render failure; this does not promise suppression of arbitrary exceptions from every caller callback. Every real window has a finite deadline. Remote requires prior explicit host setup, and native render failure ends that remote session. See [security](security.md).

### Result

| Field | Meaning |
|---|---|
| `status` | ok, cancelled, error or unavailable. |
| `exit_code` | 0, 2, 3 or 4 respectively. |
| `value` | Validated answer for a collecting verb, otherwise None. Secret values never appear here. |
| `detail` | Value-free diagnostic or unavailable reason, otherwise None. |

For confirm, ok/True and ok/False are different answers. Cancellation is not False and no non-ok result is permission. A standalone result validates shape; it does not independently prove an authoritative operator principal or resolve native harness policy.

## Tutorial: one decision, cancellation and invalid input

Run this in an installed environment on a GUI host. It displays a real question; the assertions deliberately accept either button rather than supplying an answer:

```python
from fenric import run

result = run({"verb": "confirm", "message": "Approve this demonstration?", "timeout": 60}, desktop_only=True)
if result.status == "ok":
    assert type(result.value) is bool
    # Use the actual boolean only for the exact action the caller described.
else:
    assert result.value is None
    # Cancel/error/unavailable never become an affirmative answer.
```

The documentation gate executes this same code with a scripted renderer for True, False and Cancel in a temporary workspace. That checks result handling, not a real user's approval. Run only the example you intend to present; do not create duplicate dialogs.

Invalid input is checked without a window:

```python
from fenric import run

result = run({"verb": "notify", "message": "Example", "unknown": "untrusted"})
assert result.status == "error" and result.exit_code == 3
assert result.value is None
assert "untrusted" not in result.detail
```

## Tutorial: prepare a screenshot review

From the docs directory, load the supplied manifest. This reads local images and creates a validated spec; it does not open a dialog or write answers:

```python
from fenric import load_gallery

spec = load_gallery("examples/image-comments.json")
assert spec["verb"] == "ask" and spec["presentation"] == "wizard"
assert len(spec["questions"]) == 2
assert all(question["id"] for question in spec["questions"])
```

Pass that spec to run when you intend to show the review. Results are keyed by the manifest's stable IDs, not filenames. Back/Next retains comments during the dialog; reload/persistent draft recovery is not promised. Invalid/unreadable/oversized manifests or images raise value-free SpecError from fenric.spec, rather than returning Result. Images resolve relative to the manifest. Limits and required-comment behavior are in [the gallery specification](spec.md#images-and-screenshot-galleries).

## run_integrated and IntegratedResult

```text
run_integrated(channel, *, backend=None, cancel=None) -> IntegratedResult
```

channel must be an already connected, privately bootstrapped, host-admitted AF_UNIX/SOCK_STREAM socket. Fenric duplicates it, makes its copy non-inheritable, and shuts down the one-shot connection at return; the caller closes its original descriptor. Constructing a socket yourself does not confer native authority. backend and cancel are trusted integration seams, not worker-controlled admission knobs. No ordinary stdout answer, fallback file or status-file URL is used. See the [native integration walkthrough](native-approvals.md) and [wire reference](integration.md).

| Field | Meaning |
|---|---|
| `collection` | answered, cancelled, expired, superseded, unavailable or error. |
| `response` | Complete correlated response metadata or None when the interaction failed before a response was built. |
| `worker_receipt` | Host acknowledgement outcome, otherwise unknown; not proof a model consumed it. |
| `native_delivery` | Host-reported native delivery outcome, otherwise unknown; not proof of action completion. |
| `native_evidence` | Host report such as callback-return/native-resolution; default submission-unproved. |
| `effect` | Status-only destination effect metadata; completed, not_started, not_applicable or conservative unknown. Never secret values. |

Unknown remains unknown. A reported callback delivery does not prove a tool ran, and cancellation after a completed write cannot undo it. The host must independently recheck current principal admission, exact original pending request, revocation, expiry and one-use settlement. Fenric does not discover a native harness, enroll an identity or mint owner attestations.
