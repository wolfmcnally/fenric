"""Reproduce tone.wav: an original 0.6-second integer-synthesized MIT chime.

Run from any directory; only the adjacent tone.wav is written. No playback,
external samples, networking or third-party dependencies are involved.
"""

import struct
import wave
from pathlib import Path


def samples() -> bytes:
    rate = 8000
    note_frames = 2400
    result = bytearray()
    for frequency in (400, 600):
        for frame in range(note_frames):
            phase = (frame * frequency) % rate
            triangle = 4 * abs(phase - rate // 2) - rate
            envelope = min(frame, note_frames - 1 - frame, 160)
            value = triangle * 3000 * envelope // (rate * 160)
            result.extend(struct.pack("<h", value))
    return bytes(result)


def write(destination: Path) -> None:
    with wave.open(str(destination), "wb") as output:
        output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        output.writeframes(samples())


if __name__ == "__main__":
    write(Path(__file__).with_name("tone.wav"))
