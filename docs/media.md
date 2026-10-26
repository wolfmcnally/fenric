# Picture galleries and audio previews

Use a gallery when the user should inspect pictures and optionally comment on each one. Use selected HTML when the user should listen to a caller-prepared recording beside text, a score or supporting details. Fenric presents supplied material; it does not render music, convert audio, judge an arrangement or authorize an operation when a view closes.

## Check the receiving runtime

PNG galleries are available in 0.2.0 and later. The selected-WAV extension and the audio recipe below require 0.3.0 or later; the published 0.2.0 wheel rejects WAV selections. A development build may share an older version string, so version alone does not establish an unpublished build's capabilities. Inspect the receiving runtime's current `html` selection contract and installed documentation or exact source/package provenance. Do not replace an installed runtime merely to show a preview. If it lacks the required capability, report that limitation.

The installed agent entry point is `fenric guidance read`; the complete contracts are [PNG galleries](spec.md#images-and-screenshot-galleries), [selected HTML](spec.md#html) and [remote access](commands.md#remote-access).

Before running a media invocation, create a fresh owner-only (0700) request directory outside the checkout and set `STATUS_DIR` to its absolute path. Put status files there: they can contain live bearer URLs. Never reuse a status file, symlink or public directory.

To check WAV selection without opening a window or starting remote delivery, create another fresh private directory, set `CHECK_DIR` to its absolute path, and put a minimal `index.html` and empty `probe.wav` inside it. Run `FENRIC_HEADLESS=1 fenric html --root "$CHECK_DIR" --entry index.html --file index.html --file probe.wav`. Exit 4 with exactly `unavailable: headless` means this runtime accepted WAV selection; exit 3 means refusal or invalid input (including unsupported WAV on published 0.2.0). Any other result is unknown: inspect it, do not assume support. This checks selection only, not audio validity, GUI readiness or decoding; it does not create a headless-only presentation route.

## Present a PNG gallery

Prepare regular local PNG files and a manifest beside them. Use stable IDs, useful names and honest captions describing capture provenance and unresolved defects. Do not label simulator captures as physical-device or usability acceptance. This minimal manifest is valid with the two synthetic PNGs already supplied in `docs/examples/`:

```json
{"title":"Layout review","message":"Comments are optional; submission approves no other action.","timeout":3600,"images":[{"id":"portrait","name":"Portrait layout","file":"gallery-first.png","caption":"Synthetic portrait example; not a physical-device capture."},{"id":"wide","name":"Wide layout","file":"gallery-second.png","caption":"Synthetic wide example; not a physical-device capture."}]}
```

Save it as `gallery.json` beside those PNGs, then invoke `fenric ask --gallery-file gallery.json --remote-status-file "$STATUS_DIR/gallery-status.json"` with a fresh private status path. The [worked example](examples.md#comment-on-screenshots) includes command/result captures. The terminal JSON maps each image ID to its comment, including an empty string for optional blanks. Back/Next, fitted/Natural size inspection and final review/Edit retain drafts only while the page remains open; reload, cancellation or expiry loses unsubmitted comments.

Choose a representative selection: at most 10 static PNGs, 2 MiB of PNG file bytes each and 8 MiB combined, 8192 pixels on either side, 16 million pixels per image and 24 million combined; manifest at most 64 KiB. SVG, animated PNG and remote image URLs are not gallery inputs. When a screenshot exceeds those limits, make a separate aspect-preserving resized/compressed PNG copy and retain the original and both hashes. Inspect the copy before presentation; do not crop away or retouch defects. Label changed dimensions and capture provenance. A modest maximum side such as 1100 pixels is a useful starting point, not a guarantee of byte size or readability: verify the resulting bytes, dimensions and legibility.

## Present selected WAV audio beside text

**Requires the selected-WAV capability in 0.3.0 or later.** The tiny [original audio demo](examples/audio-demo/PROVENANCE.md) contains a reproducible 0.6-second synthetic chime, a plain-text score and native browser audio controls. It is not a private recording or a song. From a source checkout or source archive, run `fenric html --root docs/examples/audio-demo --entry index.html --file index.html --file tone.wav --title "Original chime preview" --timeout 900 --remote-status-file "$STATUS_DIR/audio-status.json"` with a fresh private status path. These paths assume the repository root as the working directory; the wheel does not contain the source-documentation demo folder. No chart toolchain or extra hosting is needed.

For caller-provided material, escape the text rather than injecting it as HTML. A minimal document is:

```html
<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Listening preview</title><h1>Listening preview</h1><pre>Caller-supplied text, escaped as text.</pre><audio controls preload="metadata" src="preview.wav">Audio controls unavailable.</audio></html>
```

Select exactly this HTML file and its matching `preview.wav`, not a directory tree. Retain the exact source/audio pairing and hashes before display; relative filenames must match the selected manifest. Native controls let the user Play, pause, seek and replay within the same immutable view. Playing audio does not count as interaction: the untouched countdown keeps running. Choose a timeout longer than the full recording plus reading time. Playback is never automatic. Browser WAV decoding varies; selection alone does not validate the encoding or prove speaker audibility. The public demo exercises uncompressed PCM16 WAV. Other WAV encodings depend on the receiving browser and need their own decode/playback check; other media extensions are not supported. No MP3, AAC, streaming, transcoding, playback-speed feature or music synthesis is added by this route.

Each selected file is at most 16 MiB, the entire selected bundle at most 128 MiB and 4096 files; at most eight WAVs with at most 1024 bytes of combined percent-encoded relative WAV names. Textual resources are UTF-8. Only exact selected WAV URLs receive media permission and authenticated single-range delivery from immutable snapshots. External/data/blob media, arbitrary filesystem access and unselected files remain refused. If a full recording exceeds the limit, obtain a caller-owned complete supported derivative with explicit parent association; do not silently truncate, alter timing or downmix it.

Keep all private recordings and large audio outside repository/package inputs. This project's source archive includes only the named tiny demo WAV; the wheel includes no audio files. Packaging checks verify that allowlist and exclude synthetic private/local audio. The demo's [generator and MIT provenance](examples/audio-demo/PROVENANCE.md) are available for audit.

## Deliver the link and own the result

1. Retain one tracked invocation, its immutable material and a caller-owned start/deadline. Choose enough review time before opening; do not duplicate an already pending view.
2. When the existing authorized phone surface is configured, collect its actual availability through `--remote-status-file` or the library's `remote_status` callback. An unavailable status has no live link. Keep status files and bearer URLs private.
3. If the link cannot be delivered, report the blocker and retain this same invocation through its normal terminal result; a working desktop view remains valid. End it early only through its supported cancellation lifecycle (the CLI handles SIGINT/SIGTERM, or library callers pass their owned `cancel` event), never by clearing shared routes. Promptly relay the actual protected URL, a minimal label and truthful remaining lifetime through the operator's already authorized channel. Do not wait for a terminal result before delivering the URL. Early status contains no authoritative expiry: retain the caller deadline separately. Do not send private captions or recordings merely to relay a link.
4. Keep that same process/result handle until it terminates. Validate exit status and actual returned value; a selected control, URL visit or chat acknowledgment is not submission. Gallery comments are feedback, and HTML `ok` acknowledges presentation only. Neither grants another operation or establishes physical-device, musical or audio-timing acceptance.
5. Let the invocation clean up only its own native window, listener and private route. Preserve other views. Playback, embedded activity and opening the URL do not extend the hard deadline; cancellation/expiry grants nothing and must not silently trigger a replacement.

Missing GUI, unavailable private delivery or an unsupported runtime is a concrete blocker, not permission to install, change networking, borrow credentials, weaken authentication or use public hosting. Standalone results do not attest which person/device settled the view or replace a mandatory native approval. See [agent interaction guidance](../fenric/_guidance/fenric/guide.md) for decision and authority boundaries.
