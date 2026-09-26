from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "bin" / "runtime"
SHARED_HOST_DIR = RUNTIME / "host"
HOST_DIR = RUNTIME / ("host-linux" if sys.platform == "linux" else "host")
DLSS_DIR = RUNTIME / "dlss"
DLSSG_DIR = ROOT / "bin" / "runtime" / "dlssg"
WORKER = HOST_DIR / "nvngx.dll"
ADDON = HOST_DIR / "renodx-dlss5.addon64"
HOST_DXGI = HOST_DIR / "dxgi.dll"
RESHADE_LOG = HOST_DIR / "ReShade.log"
DLSS_SUPERRES = DLSS_DIR / "nvngx_dlss.dll"
NEURAL_RUNTIME = HOST_DIR / "nvngx_dlssnr.dll"
DLSSG_RUNTIME = DLSSG_DIR / "nvngx_dlssg.dll"
DLSSG_WORKER = DLSSG_DIR / "dlssg-worker.exe"
OUTPUTS = ROOT / "outputs"
LOGS = ROOT / "logs"
JOBS = ROOT / "jobs"


def _executable(env_name: str, command: str) -> Path:
    configured = os.environ.get(env_name)
    found = configured or shutil.which(command)
    return Path(found).resolve() if found else Path(command)


FFMPEG = _executable("DLSS_FFMPEG_PATH", "ffmpeg")
FFPROBE = _executable("DLSS_FFPROBE_PATH", "ffprobe")


def prepare_host() -> None:
    """Give Wine its own DLL search directory without changing Windows files."""
    if sys.platform != "linux":
        return
    # The executable must be a regular file: worker_launch resolves symlinks,
    # and Windows loads dxgi.dll beside the executable, not beside its link.
    # Other binaries stay shared so updates cannot leave stale NVIDIA copies.
    HOST_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("nvngx.dll", "renodx-dlss5.addon64", "nvngx_dlssnr.dll", "_nvngx.dll"):
        source = SHARED_HOST_DIR / name
        target = HOST_DIR / name
        if name == "nvngx.dll":
            with tempfile.NamedTemporaryFile(dir=HOST_DIR, delete=False) as stream:
                temporary = Path(stream.name)
            try:
                shutil.copy2(source, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        elif not target.is_symlink() or target.readlink() != source:
            # Local links include the optional driver bridge. A missing source
            # remains missing and is reported by the existing binary checks.
            with tempfile.TemporaryDirectory(dir=HOST_DIR) as directory:
                link = Path(directory) / name
                link.symlink_to(source)
                os.replace(link, target)
