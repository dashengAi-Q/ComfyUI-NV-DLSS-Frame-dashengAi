"""The Linux carrier must never replace the Windows runtime."""

import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dlss_engine.core import paths


class HostPathsTests(unittest.TestCase):
    def test_platform_selects_separate_carrier(self):
        for platform, directory in (("win32", "host"), ("linux", "host-linux")):
            with (
                self.subTest(platform=platform),
                patch.object(sys, "platform", platform),
                patch.object(paths.shutil, "which", return_value=None),
            ):
                spec = importlib.util.spec_from_file_location(
                    "test_paths", paths.__file__
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertEqual(
                    module.HOST_DXGI, module.RUNTIME / directory / "dxgi.dll"
                )
                self.assertEqual(module.WORKER.parent, module.HOST_DXGI.parent)

    @unittest.skipUnless(sys.platform == "linux", "Linux host preparation")
    def test_preparation_shares_binaries_and_preserves_both_carriers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / "host"
            linux = root / "host-linux"
            shared.mkdir()
            linux.mkdir()
            (shared / "dxgi.dll").write_bytes(b"MZwindows")
            (linux / "dxgi.dll").write_bytes(b"MZlinux")
            for name in (
                "nvngx.dll",
                "nvngx_dlssnr.dll",
                "renodx-dlss5.addon64",
                "_nvngx.dll",
            ):
                (shared / name).write_bytes(b"MZoriginal")
            with (
                patch.object(paths, "SHARED_HOST_DIR", shared),
                patch.object(paths, "HOST_DIR", linux),
            ):
                paths.prepare_host()
                self.assertFalse((linux / "nvngx.dll").is_symlink())
                self.assertEqual((linux / "nvngx.dll").resolve().parent, linux)
                for name in ("nvngx_dlssnr.dll", "renodx-dlss5.addon64", "_nvngx.dll"):
                    self.assertEqual((linux / name).resolve(), shared / name)
                (shared / "nvngx.dll").write_bytes(b"MZupdated")
                (linux / "ReShade.ini").write_text("local settings")
                paths.prepare_host()
                self.assertEqual((linux / "nvngx.dll").read_bytes(), b"MZupdated")
                self.assertEqual((linux / "ReShade.ini").read_text(), "local settings")
            self.assertEqual((shared / "dxgi.dll").read_bytes(), b"MZwindows")
            self.assertEqual((linux / "dxgi.dll").read_bytes(), b"MZlinux")

    def test_committed_carriers_match_reviewed_binaries(self):
        # CI checks out LFS pointers; local GPU installs contain the real DLLs.
        expected = {
            "host": "0cee63f9c9f13f3ac909c5b4903f4dbb4b719a7ab3b4f13b0deaf83c814b94f7",
            "host-linux": "596e4a61b96540683fdb92f80c72c96637247276a34615c3c26a35ba78725e80",
        }
        for directory, digest in expected.items():
            with self.subTest(directory=directory):
                data = (paths.RUNTIME / directory / "dxgi.dll").read_bytes()
                if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
                    self.assertIn(f"oid sha256:{digest}\n".encode(), data)
                else:
                    self.assertEqual(hashlib.sha256(data).hexdigest(), digest)

    def test_windows_preparation_does_not_touch_files(self):
        with (
            patch.object(sys, "platform", "win32"),
            patch.object(paths.shutil, "copy2") as copy,
        ):
            paths.prepare_host()
        copy.assert_not_called()
