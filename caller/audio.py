"""Turns the session recording into the two files saved with every call.

LiveKit's session recorder writes a stereo OGG/Opus file with both sides on one
timeline: channel 0 is what we heard (the clinic agent), channel 1 is what we
played (our patient bot). We keep that file as-is (handy for analysis: each side
is isolated) and also write a mono MP3 mix, which is nicer to listen to.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import av
import numpy as np


def export_recording(src: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stereo = out_dir / "recording.ogg"
    shutil.copyfile(src, stereo)
    mp3 = out_dir / "recording.mp3"
    downmix_to_mp3(stereo, mp3)
    return [stereo, mp3]


def downmix_to_mp3(src: Path, dst: Path, bitrate: int = 96_000) -> None:
    with av.open(str(src)) as inp:
        stream = inp.streams.audio[0]
        rate = stream.codec_context.sample_rate or 48_000
        chunks = []
        for frame in inp.decode(stream):
            data = frame.to_ndarray()  # planar float: (channels, samples) or packed
            if data.ndim == 2 and data.shape[0] in (1, 2) and frame.format.is_planar:
                mono = data.mean(axis=0)
            else:
                mono = data.reshape(-1, len(frame.layout.channels)).mean(axis=1)
            chunks.append(mono.astype(np.float32))
    if not chunks:
        raise RuntimeError(f"no audio in {src}")
    pcm = np.concatenate(chunks)
    # both sides sit at telephone levels; a gentle boost with clipping protection
    peak = float(np.max(np.abs(pcm))) or 1.0
    pcm = np.clip(pcm * min(1.8, 0.95 / peak), -1.0, 1.0)

    with av.open(str(dst), mode="w", format="mp3") as out:
        ost = out.add_stream("libmp3lame", rate=rate, layout="mono")
        ost.bit_rate = bitrate
        step = 1152 * 8
        for i in range(0, len(pcm), step):
            block = pcm[i : i + step].reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(block, format="fltp", layout="mono")
            frame.sample_rate = rate
            for packet in ost.encode(frame):
                out.mux(packet)
        for packet in ost.encode(None):
            out.mux(packet)
