from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

import av
import numpy as np

from ..core import ffmpeg
from ..core.gpu_selection import resolve_runtime_ai_gpu
from ..core.jobs import Cancelled, active_job
from ..core.naming import output_filename, require_available_output, validate_rename
from ..core.paths import JOBS, LOGS, OUTPUTS
from ..core.runtime import prepare_runtime, rotate_frame
from .capabilities import probe_frame_interpolation_capabilities
from .guides import DLSSGGuideGenerator
from .models import FrameInterpolationOptions, FrameInterpolationResult
from .native import DirectDLSSGSession
from .scheduler import choose_interpolation_plan, output_frame_count

@dataclass(slots=True)
class TimedFrame:
    rgba: np.ndarray
    timestamp: Fraction
    segment: int
    provenance: str
    source_index: int | None = None


class DLSSGStage:
    def __init__(
        self,
        session: DirectDLSSGSession,
        width: int,
        height: int,
        generated_count: int,
        *,
        detect_source_cuts: bool,
    ) -> None:
        self.session = session
        self.guides = DLSSGGuideGenerator(width, height)
        self.generated_count = generated_count
        self.previous: TimedFrame | None = None
        self.detect_source_cuts = detect_source_cuts
        self.scene_cuts = 0
        self.duplicates = 0

    def push(self, frame: TimedFrame) -> list[TimedFrame]:
        force_reset = self.previous is not None and frame.segment != self.previous.segment
        guide = self.guides.process(frame.rgba, force_reset=force_reset)
        if self.previous is None:
            self.session.process_frame(frame.rgba, guide.motion, frame.timestamp, reset=True)
            self.previous = frame
            return [frame]
        if self.detect_source_cuts and guide.reset and not force_reset:
            frame.segment = self.previous.segment + 1
            force_reset = True
            self.scene_cuts += 1
        if guide.duplicate:
            self.duplicates += 1
        generated = self.session.process_frame(
            frame.rgba,
            guide.motion,
            frame.timestamp,
            reset=force_reset or guide.reset,
        )
        output: list[TimedFrame] = []
        if not (force_reset or guide.reset):
            interval = frame.timestamp - self.previous.timestamp
            for index, rgba in enumerate(generated, start=1):
                output.append(
                    TimedFrame(
                        rgba,
                        self.previous.timestamp
                        + interval * Fraction(index, self.generated_count + 1),
                        frame.segment,
                        "DLSSG",
                        None,
                    )
                )
        output.append(frame)
        self.previous = frame
        return output


class NearestTimestampWriter:
    def __init__(self, nut, stream, target_rate: Fraction, output_count: int) -> None:
        self.nut = nut
        self.stream = stream
        self.target_rate = target_rate
        self.output_count = output_count
        self.next_index = 0
        self.previous: TimedFrame | None = None
        self.tie_late = False
        self.copied = 0
        self.generated = 0
        self.max_error = Fraction(0)
        self.selected_real_ids: set[int] = set()

    def _write(self, frame: TimedFrame, ideal: Fraction) -> None:
        video_frame = av.VideoFrame.from_ndarray(frame.rgba, format="rgba")
        video_frame.pts = self.next_index
        video_frame.time_base = Fraction(1, 1) / self.target_rate
        for packet in self.stream.encode(video_frame):
            self.nut.mux(packet)
        error = abs(frame.timestamp - ideal)
        self.max_error = max(self.max_error, error)
        if frame.provenance == "DLSSG":
            self.generated += 1
        else:
            self.copied += 1
            if frame.source_index is not None:
                self.selected_real_ids.add(frame.source_index)
        self.next_index += 1

    def push(self, current: TimedFrame) -> None:
        if self.previous is None:
            self.previous = current
            return
        midpoint = (self.previous.timestamp + current.timestamp) / 2
        while self.next_index < self.output_count:
            ideal = Fraction(self.next_index, 1) / self.target_rate
            if ideal < midpoint:
                self._write(self.previous, ideal)
            elif ideal == midpoint:
                selected = current if self.tie_late else self.previous
                self.tie_late = not self.tie_late
                self._write(selected, ideal)
            else:
                break
        self.previous = current

    def finish(self) -> None:
        if self.previous is None:
            raise RuntimeError("The input video contains no decodable frames.")
        while self.next_index < self.output_count:
            ideal = Fraction(self.next_index, 1) / self.target_rate
            # A finite video has no future endpoint beyond its final real frame.
            # Extend that endpoint over the final frame-duration without asking a
            # synthesizer to extrapolate beyond known motion.
            endpoint = TimedFrame(
                self.previous.rgba,
                ideal,
                self.previous.segment,
                "Source",
                self.previous.source_index,
            )
            self._write(endpoint, ideal)
        for packet in self.stream.encode():
            self.nut.mux(packet)

def _json_safe(value):
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _duration_fraction(
    metadata: dict, source_rate: Fraction, frames: int, *, cfr: bool
) -> Fraction:
    if cfr and frames > 0:
        return Fraction(frames, 1) / source_rate
    return Fraction(str(metadata["duration"]))


def _validate(options: FrameInterpolationOptions) -> None:
    _ = options.target_rate
    validate_rename(options.rename_mode, options.custom_suffix)
    ffmpeg.validate_codec_container(options.codec, options.container)
    if getattr(options, "hdr_mode", False) and not ffmpeg.hdr_mode_supported(options.codec):
        raise ValueError(
            f"HDR Mode is only available for H.265, H.265 (NVIDIA NVENC), AV1, AV1 (NVIDIA NVENC) and ProRes Proxy; current codec is {options.codec!r}."
        )
    if options.preview_seconds is not None:
        if not math.isfinite(float(options.preview_seconds)) or float(options.preview_seconds) <= 0:
            raise ValueError("Preview duration must be positive.")

def interpolate_video(
    input_path: str | os.PathLike[str],
    options: FrameInterpolationOptions | None = None,
    progress: Callable[[float, str], None] | None = None,
    *,
    output_directory: str | os.PathLike[str] | None = None,
    jobs_directory: str | os.PathLike[str] | None = None,
    logs_directory: str | os.PathLike[str] | None = None,
) -> FrameInterpolationResult:
    options = options or FrameInterpolationOptions()
    _validate(options)
    source = Path(input_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    output_root = Path(output_directory).resolve() if output_directory is not None else OUTPUTS
    jobs_root = Path(jobs_directory).resolve() if jobs_directory is not None else JOBS
    logs_root = Path(logs_directory).resolve() if logs_directory is not None else LOGS
    prepared_runtime = prepare_runtime()
    ai_gpu = resolve_runtime_ai_gpu(
        prepared_runtime.gpus, prepared_runtime.runtime_bundle, options.ai_gpu_uuid
    )
    capabilities = probe_frame_interpolation_capabilities(options.ai_gpu_uuid)
    if not capabilities.available:
        raise RuntimeError(
            "Direct NVIDIA DLSS Frame Generation is unavailable. " + capabilities.detail
        )
    with active_job() as controller:
        assert controller is not None
        started = time.perf_counter()

        def _report_progress(value: float, desc: str) -> None:
            if progress is None:
                return
            v = max(0.0, min(1.0, float(value)))
            elapsed = time.perf_counter() - started
            if 0.01 < v < 0.99 and elapsed > 0.5:
                eta = elapsed * (1 - v) / max(v, 1e-6)
                desc = f"{desc} - Time Remaining: {eta:.1f}s"
            progress(v, desc)

        timings: dict[str, float] = {}
        output: Path | None = None
        job_dir: Path | None = None
        sessions: list[DirectDLSSGSession] = []
        encoder = None
        encoder_thread = None
        try:
            probe_started = time.perf_counter()
            metadata = ffmpeg.probe_video(source, count_mode="metadata")
            is_preview = options.preview_seconds is not None
            compat_preview = is_preview and bool(getattr(options, "preview_compat", True))
            effective_hdr = (
                bool(getattr(options, "hdr_mode", False))
                and (not is_preview or not compat_preview)
                and ffmpeg.hdr_mode_supported(options.codec)
            )
            if metadata["hdr"] and not effective_hdr:
                raise ValueError(
                    "Frame Interpolation v1 accepts SDR video only. Enable HDR Mode (H.265/AV1/ProRes, 10-bit, copies input colorspace) to keep HDR, or convert PQ/HLG to SDR."
                )
            hdr_metadata = None
            if effective_hdr:
                hdr_metadata = {
                    "color_space": metadata.get("color_space", "unknown"),
                    "color_primaries": metadata.get("color_primaries", "unknown"),
                    "color_transfer": metadata.get("color_transfer", "unknown"),
                    "hdr": bool(metadata.get("hdr", False)),
                }
            source_rate = Fraction(metadata["rate"])
            cfr = bool(metadata.get("cfr", True))
            frames = int(metadata["frames"])
            if frames <= 0:
                frames = int(ffmpeg.probe_video(source, count_mode="exact")["frames"])
            full_duration = _duration_fraction(
                metadata, source_rate, frames, cfr=cfr
            )
            duration = full_duration
            if options.preview_seconds is not None:
                duration = min(duration, Fraction(str(options.preview_seconds)))
                frames = min(frames, ffmpeg.preview_frame_count(source, float(duration)))
                duration = min(duration, Fraction(frames, 1) / source_rate)
            plan = choose_interpolation_plan(
                source_rate,
                options.target_rate,
                options.engine,
                capabilities.native_multiplier,
                cfr=cfr,
            )
            output_count = output_frame_count(duration, options.target_rate)
            timings["probe_seconds"] = time.perf_counter() - probe_started
            _report_progress(0.01, f"{plan.path}: {source_rate} → {options.target_rate} FPS")

            output_root.mkdir(parents=True, exist_ok=True)
            jobs_root.mkdir(parents=True, exist_ok=True)
            logs_root.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
            job_dir = jobs_root / f"{source.stem}-DLSSFG-{stamp}-{os.getpid()}"
            job_dir.mkdir(parents=True, exist_ok=False)
            extension = {"MP4": ".mp4", "MKV": ".mkv", "MOV": ".mov"}[options.container]
            auto_stem = f"{source.stem}_{'DLSSFG_PREVIEW' if options.preview_seconds else 'DLSSFG'}_{stamp}"
            output = output_root / output_filename(
                source, extension, options.rename_mode, options.custom_suffix, auto_stem
            )
            require_available_output(output)
            # NUT preserves the exact rational frame clock between PyAV and FFmpeg;
            # Matroska's millisecond timestamp scale rounds 60000/1001 and short previews.
            temp_video = job_dir / "interpolated-video.nut"
            selected_codec = "H.264" if compat_preview else options.codec
            video_gpu = ffmpeg.resolve_video_gpu(
                prepared_runtime.gpus,
                options.video_gpu_uuid,
                selected_codec,
                int(metadata["width"]),
                int(metadata["height"]),
            )
            encoder, encoder_thread, encoder_logs, selected_encoder, quality = ffmpeg.start_encoder(
                temp_video,
                selected_codec,
                options.quality,
                controller,
                int(metadata["width"]),
                int(metadata["height"]),
                float(options.target_rate),
                None if video_gpu is None else int(video_gpu["cuda_ordinal"]),
                video_gpu is not None,
                hdr_mode=effective_hdr,
                hdr_metadata=hdr_metadata,
            )
            assert encoder.stdin is not None
            if plan.path == "Native DLSSG":
                sessions.append(
                    DirectDLSSGSession(
                        int(metadata["width"]), int(metadata["height"]), frames,
                        plan.generated_per_interval, controller,
                    )
                )
            elif plan.path == "Cascade":
                for stage_index in range(plan.cascade_stages):
                    expected = max(1, (frames - 1) * (1 << stage_index) + 1)
                    sessions.append(
                        DirectDLSSGSession(
                            int(metadata["width"]), int(metadata["height"]), expected, 1, controller
                        )
                    )
            stages = [
                DLSSGStage(
                    session,
                    int(metadata["width"]),
                    int(metadata["height"]),
                    plan.generated_per_interval if plan.path == "Native DLSSG" else 1,
                    detect_source_cuts=index == 0,
                )
                for index, session in enumerate(sessions)
            ]
            nut = av.open(encoder.stdin, mode="w", format="nut")
            raw_stream = nut.add_stream("rawvideo", rate=options.target_rate)
            raw_stream.width = int(metadata["width"])
            raw_stream.height = int(metadata["height"])
            raw_stream.pix_fmt = "rgba"
            raw_stream.time_base = Fraction(1, 1) / options.target_rate
            writer = NearestTimestampWriter(nut, raw_stream, options.target_rate, output_count)

            decoded = 0
            segment = 0
            discontinuities = 0
            last_timestamp: Fraction | None = None
            nominal_interval = Fraction(1, 1) / source_rate
            container = av.open(str(source))
            try:
                stream = container.streams.video[0]
                stream.thread_type = "AUTO"
                for frame in container.decode(stream):
                    if decoded >= frames:
                        break
                    if controller.cancel.is_set():
                        raise Cancelled("Frame interpolation stopped by user.")
                    timestamp = (
                        Fraction(frame.pts) * Fraction(stream.time_base)
                        if frame.pts is not None and stream.time_base is not None
                        else Fraction(decoded, 1) / source_rate
                    )
                    if last_timestamp is not None and (
                        timestamp <= last_timestamp or timestamp - last_timestamp > nominal_interval * 2
                    ):
                        segment += 1
                        discontinuities += 1
                    last_timestamp = timestamp
                    rgba = rotate_frame(frame.to_ndarray(format="rgba"), int(metadata["rotation"]))
                    timed = TimedFrame(
                        np.ascontiguousarray(rgba), timestamp, segment, "Source", decoded
                    )
                    items = [timed]
                    for stage in stages:
                        next_items: list[TimedFrame] = []
                        for item in items:
                            next_items.extend(stage.push(item))
                        items = next_items
                    for item in items:
                        writer.push(item)
                    decoded += 1
                    if decoded % max(1, frames // 100) == 0:
                        _report_progress(0.04 + 0.82 * decoded / frames, f"DLSSG source frame {decoded}/{frames}")
            finally:
                container.close()
            writer.finish()
            nut.close()
            encoder.stdin.close()
            for session in sessions:
                session.close()
            sessions.clear()
            encoder_code = encoder.wait(timeout=180)
            encoder_thread.join(timeout=2)
            controller.unregister(encoder)
            if encoder_code:
                raise RuntimeError("Video encoder failed:\n" + "\n".join(encoder_logs[-40:]))
            _report_progress(0.9, "Muxing original audio, subtitles, chapters, and metadata")
            ffmpeg.final_mux(
                temp_video,
                source,
                output,
                options.container,
                controller,
                preserve_supported_subtitles=True,
            )
            verified = ffmpeg.probe_video(output, count_mode="packets")
            if int(verified["frames"]) != output_count:
                verified = ffmpeg.probe_video(output, count_mode="exact")
            if int(verified["frames"]) != output_count:
                raise RuntimeError(
                    f"Output verification found {verified['frames']} frames; expected {output_count}."
                )
            if Fraction(verified["rate"]) != options.target_rate:
                raise RuntimeError(
                    f"Output verification found {verified['rate']} FPS; expected {options.target_rate}."
                )
            scene_cuts = sum(stage.scene_cuts for stage in stages)
            duplicate_intervals = sum(stage.duplicates for stage in stages)
            dropped_frames = max(0, decoded - len(writer.selected_real_ids))
            elapsed = time.perf_counter() - started
            report = {
                "status": "success",
                "input": str(source),
                "output": str(output),
                "options": _json_safe(asdict(options)),
                "capabilities": asdict(capabilities),
                "ai_gpu": ai_gpu,
                "video_gpu": video_gpu,
                "plan": {key: str(value) if isinstance(value, Fraction) else value for key, value in asdict(plan).items()},
                "input_frames": decoded,
                "output_frames": output_count,
                "copied_frames": writer.copied,
                "generated_frames": writer.generated,
                "dropped_frames": dropped_frames,
                "maximum_temporal_approximation_seconds": float(writer.max_error),
                "scene_cuts": scene_cuts,
                "duplicate_intervals": duplicate_intervals,
                "timestamp_discontinuities": discontinuities,
                "encoder": selected_encoder,
                "encoding_quality": quality,
                "elapsed_seconds": elapsed,
            }
            report_path = logs_root / f"DLSSFG_{source.stem}_{stamp}.report.json"
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            _report_progress(1.0, "Frame interpolation complete")
            return FrameInterpolationResult(
                output_path=str(output.resolve()),
                report_path=str(report_path.resolve()),
                selected_path=plan.path,
                native_multiplier=plan.native_multiplier,
                cascade_stages=plan.cascade_stages,
                copied_frames=writer.copied,
                generated_frames=writer.generated,
                dropped_frames=dropped_frames,
                output_frames=output_count,
                maximum_temporal_approximation_seconds=float(writer.max_error),
                scene_cuts=scene_cuts,
                elapsed_seconds=elapsed,
                timings=timings,
            )
        except BaseException as exc:
            if output and output.exists():
                with suppress(OSError):
                    output.unlink()
            if controller.cancel.is_set():
                raise Cancelled("Frame interpolation stopped by user.") from exc
            if isinstance(exc, Cancelled):
                raise
            if not isinstance(exc, Exception):
                raise
            logs_root.mkdir(parents=True, exist_ok=True)
            failure_stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
            failure_path = logs_root / f"DLSSFG_{source.stem}_{failure_stamp}.failure.json"
            failure_path.write_text(
                json.dumps(
                    {
                        "status": "failure",
                        "input": str(source),
                        "options": _json_safe(asdict(options)),
                        "error": str(exc),
                        "capabilities": asdict(capabilities),
                        "worker_logs": [session.log_text() for session in sessions],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            raise RuntimeError(f"{exc}\nDiagnostic report: {failure_path.resolve()}") from exc
        finally:
            for session in sessions:
                with suppress(Exception):
                    session.close()
            if encoder is not None:
                if encoder.poll() is None:
                    with suppress(OSError):
                        encoder.terminate()
                try:
                    encoder.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    with suppress(OSError):
                        encoder.kill()
                    with suppress(OSError, subprocess.TimeoutExpired):
                        encoder.wait(timeout=5)
                if encoder.stdin and not encoder.stdin.closed:
                    with suppress(OSError):
                        encoder.stdin.close()
                controller.unregister(encoder)
                if encoder_thread is not None:
                    encoder_thread.join(timeout=2)
                    if encoder.stderr and not encoder_thread.is_alive():
                        encoder.stderr.close()
            if job_dir and job_dir.parent == jobs_root and job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)
