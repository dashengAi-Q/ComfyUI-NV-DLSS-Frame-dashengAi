from __future__ import annotations

import json
import os
import subprocess
from fractions import Fraction
from pathlib import Path

import av

from ..paths import FFPROBE

def _run_json(command: list[str]) -> dict:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Media probe failed")
    return json.loads(result.stdout)


def probe_video(
    path: str | os.PathLike[str], *, count_mode: str = "exact"
) -> dict:
    """Probe video metadata, optionally counting decoded frames or packets.

    The default remains the legacy exact decoded-frame count for external callers.
    Conversion paths use metadata first and pay for exact counting only as fallback.
    """
    if count_mode not in {"metadata", "exact", "packets"}:
        raise ValueError(f"Unknown video count mode: {count_mode!r}.")
    count_args = {
        "metadata": [],
        "exact": ["-count_frames"],
        "packets": ["-count_packets"],
    }[count_mode]
    data = _run_json(
        [
            str(FFPROBE),
            "-v",
            "error",
            *count_args,
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=index,codec_name,width,height,avg_frame_rate,r_frame_rate,time_base,duration,nb_frames,nb_read_frames,nb_read_packets,color_primaries,color_transfer,color_space:stream_tags=rotate:stream_side_data=rotation",
            "-show_entries",
            "format=duration,format_name",
            "-of",
            "json",
            str(path),
        ]
    )
    streams = data.get("streams") or []
    if not streams:
        raise ValueError("The selected file contains no decodable video stream.")
    stream = streams[0]
    rotation = int((stream.get("tags") or {}).get("rotate", 0) or 0)
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            rotation = int(side["rotation"] or 0)
    rotation %= 360
    width, height = int(stream["width"]), int(stream["height"])
    if rotation in (90, 270):
        width, height = height, width
    if count_mode == "packets":
        frames = int(stream.get("nb_read_packets") or stream.get("nb_frames") or 0)
        frame_count_source = "packets" if stream.get("nb_read_packets") else "metadata"
    elif count_mode == "exact":
        frames = int(stream.get("nb_read_frames") or stream.get("nb_frames") or 0)
        frame_count_source = "decoded" if stream.get("nb_read_frames") else "metadata"
    else:
        frames = int(stream.get("nb_frames") or 0)
        frame_count_source = "metadata" if frames > 0 else "unavailable"
    if count_mode == "exact" and frames <= 0:
        raise ValueError("Could not determine an exact frame count for this video.")
    average_rate_text = stream.get("avg_frame_rate") or "0/0"
    nominal_rate_text = stream.get("r_frame_rate") or "0/0"
    rate_text = average_rate_text if average_rate_text != "0/0" else nominal_rate_text
    if rate_text == "0/0":
        rate_text = "30/1"
    rate = Fraction(rate_text) if rate_text != "0/0" else Fraction(30, 1)
    nominal_rate = (
        Fraction(nominal_rate_text)
        if nominal_rate_text != "0/0"
        else rate
    )
    transfer = stream.get("color_transfer") or "unknown"
    primaries = stream.get("color_primaries") or "unknown"
    color_space = stream.get("color_space") or "unknown"
    return {
        "width": width,
        "height": height,
        "coded_width": int(stream["width"]),
        "coded_height": int(stream["height"]),
        "rotation": rotation,
        "frames": frames,
        "frame_count_source": frame_count_source,
        "fps": float(rate),
        "rate": rate,
        "nominal_rate": nominal_rate,
        "cfr": rate == nominal_rate,
        "time_base": Fraction(stream.get("time_base") or "1/1000"),
        "duration": float(
            (data.get("format") or {}).get("duration") or stream.get("duration") or 0
        ),
        "codec": stream.get("codec_name") or "unknown",
        "format": (data.get("format") or {}).get("format_name") or "unknown",
        "color_transfer": transfer,
        "color_primaries": primaries,
        "color_space": color_space,
        "hdr": transfer in {"smpte2084", "arib-std-b67"},
    }

def preview_frame_count(source: Path, seconds: float) -> int:
    """Count frames whose presentation times fall within the opening interval."""
    container = av.open(str(source))
    try:
        stream = container.streams.video[0]
        rate = float(stream.average_rate or 30)
        first_time: float | None = None
        count = 0
        for frame in container.decode(stream):
            timestamp = (
                float(frame.pts * stream.time_base)
                if frame.pts is not None and stream.time_base is not None
                else count / rate
            )
            if first_time is None:
                first_time = timestamp
            if count and timestamp - first_time >= seconds:
                break
            count += 1
        if count == 0:
            raise RuntimeError("The input video contains no decodable preview frames.")
        return count
    finally:
        container.close()
