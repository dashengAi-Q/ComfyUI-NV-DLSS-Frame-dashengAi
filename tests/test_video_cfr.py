from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import av

from dlss_engine.core import ffmpeg
from dlss_engine.core.paths import FFMPEG, FFPROBE
from dlss_engine.video import processor
from dlss_engine.video.models import ConversionOptions


class PassthroughSession:
    def __init__(self, **kwargs):
        self.render_width = kwargs["input_width"]
        self.render_height = kwargs["input_height"]
        self.minimum_width = 64
        self.minimum_height = 64
        self.maximum_width = 7680
        self.maximum_height = 4320
        self.setup_result = 1
        self.applied_dlss_model_preset = int(
            kwargs["native_settings"]["dlss_model_preset"]
        )
        self.worker_logs = []
        self.worker_log_dropped_lines = 0
        self.closed = False

    def process(self, *, rgba, pts, **_kwargs):
        return rgba, pts

    def close(self):
        self.closed = True

    def abort(self):
        self.closed = True

    def reshade_log_text(self):
        return ""


@unittest.skipUnless(
    shutil.which(str(FFMPEG)) and shutil.which(str(FFPROBE)),
    "FFmpeg and FFprobe are required",
)
class VideoCFRTests(unittest.TestCase):
    def test_upscale_output_keeps_exact_cfr_for_dlssg(self):
        with tempfile.TemporaryDirectory(prefix="dlss-cfr-") as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run(
                [
                    str(FFMPEG),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=64x64:rate=24",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=48000",
                    "-frames:v",
                    "362",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(source),
                ],
                check=True,
                timeout=30,
            )
            runtime = SimpleNamespace(
                gpus=(), runtime_bundle={"addon": {"release": "test"}}
            )
            feature_evidence = {
                "nr_upscaling_active": False,
                "nr_native_fallback": True,
                "carrier_create_result": "test",
                "evidence": [],
            }
            with (
                patch.object(processor, "prepare_runtime", return_value=runtime),
                patch.object(
                    processor,
                    "resolve_runtime_ai_gpu",
                    return_value={"display_name": "Test GPU"},
                ),
                patch.object(processor.ffmpeg, "resolve_video_gpu", return_value=None),
                patch.object(processor, "DLSSFrameSession", PassthroughSession),
                patch.object(
                    processor,
                    "verify_feature_18",
                    return_value=feature_evidence,
                ),
            ):
                result = processor.convert_video(
                    source,
                    ConversionOptions(upscaling_factor=1.0, quality="Max"),
                    output_directory=root / "output",
                    jobs_directory=root / "jobs",
                    logs_directory=root / "logs",
                )

            metadata = ffmpeg.probe_video(result.output_path, count_mode="metadata")
            self.assertEqual(metadata["frames"], 362)
            self.assertEqual(metadata["rate"], 24)
            self.assertEqual(metadata["nominal_rate"], 24)
            self.assertTrue(metadata["cfr"])
            with av.open(result.output_path) as output:
                self.assertEqual(len(output.streams.audio), 1)


if __name__ == "__main__":
    unittest.main()
