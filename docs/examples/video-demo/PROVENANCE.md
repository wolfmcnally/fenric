# Synthetic video fixtures

Created 2026-11-08 using FFmpeg 8.1.2, from its synthetic test pattern and sine source. These contain no film, recording or third-party source material. Both are 160 × 90, 12 frames/second and three seconds, with a 440 Hz tone; MP4 uses H.264/AAC and WebM uses VP9/Opus. The player sample selects MP4; WebM is a separate test fixture, not an implicit fallback selection.

Generation commands (FFmpeg is a development fixture tool, never a Fenric runtime dependency):

```sh
ffmpeg -f lavfi -i testsrc2=size=160x90:rate=12:duration=3 -f lavfi -i sine=frequency=440:sample_rate=48000:duration=3 -c:v libx264 -pix_fmt yuv420p -preset slow -crf 32 -c:a aac -b:a 32k -movflags +faststart -map_metadata -1 sample.mp4
ffmpeg -f lavfi -i testsrc2=size=160x90:rate=12:duration=3 -f lavfi -i sine=frequency=440:sample_rate=48000:duration=3 -c:v libvpx-vp9 -crf 40 -b:v 0 -c:a libopus -b:a 24k -map_metadata -1 sample.webm
```

Encoder versions may produce different bytes. Transport tests compare against the committed immutable fixtures; playback tests observe decoding and seeking without claiming speaker audibility or cross-platform codec availability.
