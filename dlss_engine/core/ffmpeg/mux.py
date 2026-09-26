from __future__ import annotations

import math
import subprocess
from pathlib import Path

from ..jobs import JobController
from ..paths import FFMPEG, FFPROBE
from .probe import _run_json

def _probe_rendered_duration(path: Path) -> float:
    """Read the intermediate video's duration without decoding/counting its frames."""
    data = _run_json(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=duration:format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    streams = data.get("streams") or []
    values = [
        (streams[0] if streams else {}).get("duration"),
        (data.get("format") or {}).get("duration"),
    ]
    for raw_value in values:
        try:
            duration = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(duration) and duration > 0:
            return duration
    raise RuntimeError(
        "Could not determine the rendered video's duration for the final audio mux."
    )


def final_mux(
    temp_video: Path,
    source: Path,
    output: Path,
    container: str,
    controller: JobController | None = None,
    preserve_supported_subtitles: bool = False,
) -> None:
    duration = _probe_rendered_duration(temp_video)
    if container == "MKV":
        maps = ["-map", "0:v:0", "-map", "1:a?", "-map", "1:s?"]
        streams = ["-c:v", "copy", "-c:a", "copy", "-c:s", "copy"]
    else:
        maps = ["-map", "0:v:0", "-map", "1:a?"]
        streams = [
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
        ]
        if preserve_supported_subtitles:
            subtitle_data = _run_json(
                [
                    str(FFPROBE),
                    "-v",
                    "error",
                    "-select_streams",
                    "s",
                    "-show_entries",
                    "stream=index,codec_name",
                    "-of",
                    "json",
                    str(source),
                ]
            )
            supported_text = {"ass", "ssa", "subrip", "mov_text", "webvtt", "text"}
            subtitle_indices = [
                int(stream["index"])
                for stream in subtitle_data.get("streams") or []
                if stream.get("codec_name") in supported_text
            ]
            for stream_index in subtitle_indices:
                maps.extend(["-map", f"1:{stream_index}"])
            if subtitle_indices:
                streams.extend(["-c:s", "mov_text"])
    command = [
        str(FFMPEG),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-i",
        str(temp_video),
        "-t",
        f"{duration:.9f}",
        "-i",
        str(source),
        *maps,
        "-map_metadata",
        "1",
        "-map_chapters",
        "1",
        *streams,
        str(output),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if controller is not None:
        controller.register(process)
    try:
        _stdout, stderr = process.communicate()
    finally:
        if controller is not None:
            controller.unregister(process)
    if process.returncode:
        raise RuntimeError("Final audio/metadata mux failed:\n" + stderr[-4000:])
