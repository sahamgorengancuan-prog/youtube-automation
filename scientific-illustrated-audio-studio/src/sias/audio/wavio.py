"""Tolerant WAV reader.

Every audio gate in SIAS reads samples through Python's `wave`, which refuses
WAVE_FORMAT_EXTENSIBLE (`unknown format: 65534`). FFmpeg writes exactly that for
filtered output (loudnorm, amix) even when the codec is `pcm_s16le` — so a
mastered narration would crash the gates that exist to protect the render.

The payload in those files IS plain PCM; only the `fmt ` chunk differs. We
rewrite that chunk in memory and hand `wave` a stream it understands. Anything
that is not really PCM underneath is still rejected — this widens what we can
read, never what we accept.
"""

from __future__ import annotations

import io
import struct
import wave
from pathlib import Path

from ..exceptions import AssetIntegrityError

WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_EXTENSIBLE = 0xFFFE


def _demote_extensible(raw: bytes) -> bytes:
    """Rewrite an extensible `fmt ` chunk as a classic 16-byte PCM one."""
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise AssetIntegrityError("not a RIFF/WAVE file", stage="audio")
    out = bytearray(raw)
    pos = 12
    while pos + 8 <= len(out):
        chunk_id = bytes(out[pos:pos + 4])
        size = struct.unpack_from("<I", out, pos + 4)[0]
        body = pos + 8
        if chunk_id == b"fmt ":
            tag = struct.unpack_from("<H", out, body)[0]
            if tag != WAVE_FORMAT_EXTENSIBLE:
                return bytes(out)
            # SubFormat GUID lives at body+24; its first 2 bytes are the real tag.
            if size < 40:
                raise AssetIntegrityError("extensible fmt chunk truncated", stage="audio")
            sub_tag = struct.unpack_from("<H", out, body + 24)[0]
            if sub_tag != WAVE_FORMAT_PCM:
                raise AssetIntegrityError(
                    f"WAV subformat {sub_tag:#06x} is not PCM — refusing to reinterpret it",
                    stage="audio",
                )
            # Keep channels/rate/block-align/bits verbatim; only the tag changes.
            fmt = struct.pack("<H", WAVE_FORMAT_PCM) + bytes(out[body + 2:body + 16])
            rebuilt = bytearray(out[:pos]) + b"fmt " + struct.pack("<I", 16) + fmt + out[body + size:]
            struct.pack_into("<I", rebuilt, 4, len(rebuilt) - 8)  # fix RIFF size
            return bytes(rebuilt)
        pos = body + size + (size & 1)
    raise AssetIntegrityError("WAV has no fmt chunk", stage="audio")


def open_wav(path: str | Path) -> wave.Wave_read:
    """`wave.open` that also accepts FFmpeg's WAVE_FORMAT_EXTENSIBLE output."""
    p = Path(path)
    try:
        return wave.open(str(p), "rb")
    except wave.Error as exc:
        if "65534" not in str(exc):
            raise AssetIntegrityError(f"audio not decodable as WAV: {p} ({exc})", stage="audio") from exc
    try:
        return wave.open(io.BytesIO(_demote_extensible(p.read_bytes())), "rb")
    except wave.Error as exc:
        raise AssetIntegrityError(
            f"audio not decodable as WAV even after normalising the fmt chunk: {p} ({exc})",
            stage="audio",
        ) from exc
