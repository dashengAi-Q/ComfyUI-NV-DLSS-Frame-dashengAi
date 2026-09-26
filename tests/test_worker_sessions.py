import os
import subprocess
import sys
import tempfile
import threading
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import numpy as np

from dlss_engine.core import workers
from dlss_engine.core.jobs import (
    BoundedLogBuffer,
    JobController,
    drain_bounded_text,
)
from dlss_engine.core.runtime import DLSSFrameSession
from dlss_engine.core.workers import WorkerProcess
from dlss_engine.frame_interpolation import capabilities, native


@unittest.skipUnless(sys.platform == "linux", "Linux worker integration")
class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enterContext(
            patch.object(
                workers,
                "CONFIG_PATH",
                Path(self.temp.name) / "linux-runtime.json",
            )
        )
        root = Path(self.temp.name)
        self.worker = root / "worker.exe"
        self.worker.write_bytes(b"MZ" + bytes(64))
        prefix = root / "prefix"
        (prefix / "drive_c/windows/system32").mkdir(parents=True)
        launcher = root / "wine"
        fixture = Path(__file__).parent / "fixtures/wine_worker.py"
        launcher.write_text(f"#!{sys.executable}\n" + fixture.read_text())
        launcher.chmod(0o755)
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "WINEPREFIX": str(prefix),
                    "DLSS_WINE_PATH": str(launcher),
                },
            )
        )
        for module in (capabilities, native):
            self.enterContext(
                patch.object(module, "DLSSG_WORKER", self.worker)
            )
            self.enterContext(patch.object(module, "RUNTIME_DIR", root))
        self.enterContext(
            patch.object(capabilities, "DLSSG_RUNTIME", self.worker)
        )

    def test_probe_reads_real_subprocess_json(self):
        result = capabilities._probe_worker()
        self.assertTrue(result["available"])
        self.assertEqual(result["multi_frame_count_max"], 1)

    def test_probe_preserves_native_failure(self):
        with patch.dict(os.environ, {"TEST_WORKER_MODE": "error"}):
            with self.assertRaisesRegex(RuntimeError, "NGX initialization"):
                capabilities._probe_worker()

    def test_probe_timeout_reaps_worker(self):
        processes = []

        def launch(*args, **kwargs):
            process = WorkerProcess(*args, **kwargs)
            processes.append(process)
            return process

        with patch.dict(os.environ, {"TEST_WORKER_MODE": "hang"}):
            with patch.object(capabilities, "WorkerProcess", launch):
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    capabilities._probe_worker(timeout=0.2)
        self.assertIsNotNone(processes[0].returncode)

    def test_session_streams_multiple_frames_larger_than_pipe_buffer(self):
        controller = JobController()
        color = np.arange(256 * 256 * 4, dtype=np.uint8).reshape(256, 256, 4)
        motion = np.zeros((256, 256, 2), dtype=np.float16)
        with native.DirectDLSSGSession(256, 256, 3, 1, controller) as session:
            for index in range(3):
                result = session.process_frame(
                    color, motion, Fraction(index, 24), reset=index == 0
                )
                self.assertEqual(len(result), 1)
                np.testing.assert_array_equal(result[0], color)
        self.assertEqual(session.process.returncode, 0)
        self.assertFalse(controller._processes)

    def test_setup_eof_releases_process(self):
        controller = JobController()
        with patch.dict(os.environ, {"TEST_WORKER_MODE": "eof"}):
            with self.assertRaisesRegex(RuntimeError, "closed its output"):
                native.DirectDLSSGSession(64, 64, 1, 1, controller)
        self.assertFalse(controller._processes)

    def test_upscale_shutdown_timeout_reaps_stubborn_worker(self):
        # A real peer ignores SIGTERM after announcing readiness, exercising
        # escalation to SIGKILL rather than mocking process termination.
        with patch.dict(os.environ, {"TEST_WORKER_MODE": "shutdown-hang"}):
            process = WorkerProcess(
                self.worker,
                "--video",
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.assertEqual(process.stdout.readline(), b"ready\n")
        controller = JobController()
        controller.register(process)
        session = DLSSFrameSession.__new__(DLSSFrameSession)
        session.worker = process
        session.controller = controller
        session.closed = False
        session._worker_log_buffer = BoundedLogBuffer()
        session.worker_thread = threading.Thread(
            target=drain_bounded_text,
            args=(process.stderr, session._worker_log_buffer),
            daemon=True,
        )
        session.worker_thread.start()
        wait = process.wait
        with patch.object(
            process, "wait", side_effect=lambda timeout=None: wait(timeout=0.1)
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                session.close()
        self.assertEqual(process.returncode, -9)
        self.assertFalse(controller._processes)
        self.assertTrue(session.closed)
        self.assertFalse(session.worker_thread.is_alive())
        self.assertTrue(
            all(
                stream.closed
                for stream in (process.stdin, process.stdout, process.stderr)
            )
        )
        session.close()
        session.abort()

    def test_linux_report_does_not_recommend_hags(self):
        capabilities.clear_capability_cache()
        self.addCleanup(capabilities.clear_capability_cache)
        with patch.object(
            capabilities,
            "detect_gpu",
            return_value={
                "name": "Test RTX GPU",
                "driver": "test",
                "uuid": "test",
            },
        ):
            result = capabilities.probe_frame_interpolation_capabilities()
        self.assertTrue(result.available)
        self.assertNotIn("HAGS is disabled", result.detail)
        self.assertIn("Wine", result.detail)


if __name__ == "__main__":
    unittest.main()
