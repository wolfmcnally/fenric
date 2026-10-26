# Original chime demonstration

Created for Fenric on 2026-10-26 UTC. Author and copyright holder: Wolf McNally. The generator, HTML and synthesized sound are licensed under the repository's MIT license (see `../../../LICENSE`). No third-party samples, recordings, composition or source material are used. This is a generic two-frequency technical test tone, not an arrangement or a private recording.

`generate.py` uses only Python's standard library and deterministic integer triangle-wave synthesis: 400 Hz followed by 600 Hz, 0.3 seconds each, with short linear amplitude ramps. `tone.wav` is complete mono signed PCM16 at 8000 samples/second, 4800 frames, 0.6 seconds, 9644 bytes. There is no automatic playback, capture, network or credential access. Reproduce it with `python docs/examples/audio-demo/generate.py` from the repository root, or run the script by absolute path; it writes only its adjacent `tone.wav`.

The source archive includes this tiny demo and generator. The wheel contains the agent recipes but no audio file. The package inventory and tests allow only this exact demo path and verify its bytes and small size; synthetic private/large audio placed in ignored local evidence is excluded. Real recordings, private score/audio manifests, runtime receipts and large media are not examples or release inputs.

The [listening recipe](../../media.md#present-selected-wav-audio-beside-text) requires the selected-WAV capability in 0.3.0 or later; the published Fenric 0.2.0 runtime does not support it. Preparing this fixture does not install that capability or prove a speaker, browser or physical-device test.
