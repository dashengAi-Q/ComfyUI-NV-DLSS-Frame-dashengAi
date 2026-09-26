#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ComfyUI-NV-DLSS-Frame-dashengAi - large runtime files installer.

The large NVIDIA runtime binaries (~234 MB) are NOT stored directly in this
GitHub repository (which must stay under 50 MB). They are tracked with Git LFS
and hosted on Hugging Face; a cloud-drive bundle is the manual fallback.

Fetch order used by this script:

  1. Skip any file that already exists with the correct SHA-256.
  2. Copy from the local staging folder ``large_files/`` if present (offline
     restore, used on the machine that owns the staging folder).
  3. Run ``git lfs pull`` (the primary mechanism): pointer files in the clone
     are resolved against the Hugging Face endpoint from ``.lfsconfig``.
  4. Download any remaining file directly over HTTPS from Hugging Face.
  5. Report failures with the cloud-drive manual fallback instructions.

Usage:
    python scripts/download_large_files.py            # fetch all missing files
    python scripts/download_large_files.py --check    # only verify installation
    python scripts/download_large_files.py --no-lfs   # skip git-lfs, use HTTPS only
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# DISTRIBUTION CONFIGURATION
# ---------------------------------------------------------------------------
# Hugging Face repository that stores the runtime files. Upload the CONTENTS
# of the local ``large_files/`` folder into this repo's root, keeping the
# exact same relative paths (bin/runtime/host/..., bin/runtime/dlss/...).
HF_REPO_ID = "dashengAi/ComfyUI-NV-DLSS-Frame-dashengAi-runtime"

# Cloud-drive shared link (zip of the ``large_files/`` folder) used when
# Hugging Face is not reachable.
CLOUD_DRIVE_URL = "https://pan.quark.cn/s/c65a50478105"
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
LARGE_DIR = ROOT / "large_files"

# (relative destination under the plugin root, expected size in bytes, sha256)
MANIFEST = [
    {
        "rel": "bin/runtime/host/nvngx_dlssnr.dll",
        "size": 165830144,
        "sha256": "6EB209E764F39872625DEBD6ABAF45E2BB6322F6F270F781F70C059AE30B3927",
    },
    {
        "rel": "bin/runtime/dlss/nvngx_dlss.dll",
        "size": 58956400,
        "sha256": "C85F971CE023C9F3492FC7455F0B01A24BA18EA39636407A846902C4360B0B7E",
    },
    {
        "rel": "bin/runtime/dlssg/nvngx_dlssg.dll",
        "size": 7519856,
        "sha256": "135EAF0733C1E37381A8C28ABCF7A862404A54132B81787C04E35D09EFC5E36F",
    },
    {
        "rel": "bin/runtime/host/dxgi.dll",
        "size": 5592064,
        "sha256": "0CEE63F9C9F13F3AC909C5B4903F4DBB4B719A7AB3B4F13B0DEAF83C814B94F7",
    },
    {
        "rel": "bin/runtime/host-linux/dxgi.dll",
        "size": 5591040,
        "sha256": "596E4A61B96540683FDB92F80C72C96637247276A34615C3C26A35BA78725E80",
    },
    {
        "rel": "bin/runtime/host/renodx-dlss5.addon64",
        "size": 1732608,
        "sha256": "D5ADF82EB44B065F4C590AC91FE824BAB07AFEA0EB9F994BDE936710C8593952",
    },
    {
        "rel": "bin/runtime/host/nvngx.dll",
        "size": 80384,
        "sha256": "58191F4D38288C6BFBDA47EF56911D32052A9789E65714F4583F426E01464638",
    },
    {
        "rel": "bin/runtime/dlssg/dlssg-worker.exe",
        "size": 66560,
        "sha256": "8A747F9ED613842D5B8B34A811AD43BC1A9466540E2E5A0C8EF4005F0DB9E384",
    },
]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _manifest_sha256_for(rel: str) -> str | None:
    for item in MANIFEST:
        if item["rel"] == rel:
            return item["sha256"]
    return None


def file_is_ok(path: Path) -> bool:
    expected = _manifest_sha256_for(path.resolve().relative_to(ROOT.resolve()).as_posix())
    if expected is None or not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        return sha256_of(path) == expected
    except OSError:
        return False


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=120) as response, part.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        received = 0
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            received += len(chunk)
            if total:
                percent = received * 100 // total
                print(
                    f"    {percent:3d}%  {received / 1048576:.1f} / {total / 1048576:.1f} MB",
                    flush=True,
                )
    part.replace(destination)


def try_git_lfs_pull() -> bool:
    """Primary mechanism: resolve the LFS pointers against Hugging Face.

    Returns True when a git repo with LFS pointers is present and git-lfs is
    installed (whether or not the network fetch succeeded); False when this is
    not a git clone, so callers fall back to the HTTPS downloader.
    """
    if not (ROOT / ".git").exists():
        return False
    if shutil.which("git") is None:
        return False
    probe = subprocess.run(
        ["git", "lfs", "version"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode != 0:
        print("[LFS]    git-lfs is not installed; falling back to HTTPS download.")
        return False
    print("[LFS]    Running `git lfs pull` (Hugging Face endpoint from .lfsconfig)...")
    try:
        result = subprocess.run(
            ["git", "lfs", "pull"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except subprocess.TimeoutExpired:
        print("[LFS]    `git lfs pull` timed out.")
        return True
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())
    if result.returncode == 0:
        print("[LFS]    `git lfs pull` finished.")
    else:
        print("[LFS]    `git lfs pull` failed (network or object missing); will try HTTPS.")
    return True


def ensure_one(item: dict, *, check_only: bool, allow_https: bool) -> bool:
    rel = item["rel"]
    destination = ROOT / Path(rel)
    if file_is_ok(destination):
        print(f"[OK]     {rel}")
        return True

    if check_only:
        print(f"[MISS]   {rel}")
        return False

    # 1) Offline restore from the local staging folder.
    staged = LARGE_DIR / Path(rel)
    if staged.exists() and sha256_of(staged) == item["sha256"]:
        print(f"[COPY]   {rel}  <- large_files/ (offline restore)")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged, destination)
        return True

    # 2) Direct HTTPS download from Hugging Face (no git-lfs required).
    if allow_https and HF_REPO_ID and not HF_REPO_ID.startswith("YOUR_HF_USERNAME"):
        url = f"https://huggingface.co/{HF_REPO_ID}/resolve/main/{rel}"
        print(f"[FETCH]  {rel}")
        print(f"         {url}")
        for attempt in (1, 2):
            try:
                _download(url, destination)
                if sha256_of(destination) == item["sha256"]:
                    print(f"[DONE]   {rel}")
                    return True
                print(f"    SHA-256 mismatch after download (attempt {attempt}).")
                destination.unlink(missing_ok=True)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                print(f"    download failed (attempt {attempt}): {exc}")
        print(f"[FAIL]   {rel}  (Hugging Face unreachable or file missing)")

    print(f"[MISS]   {rel}")
    return False


def manual_instructions(failed: list[str]) -> None:
    print("\n" + "=" * 72)
    print("Some runtime files could not be fetched automatically.")
    print("Manual fallback (cloud drive):")
    if CLOUD_DRIVE_URL:
        print(f"  1. Download the large-files bundle from:\n     {CLOUD_DRIVE_URL}")
    else:
        print("  1. Download the large-files bundle (see the README cloud-drive link).")
    print("  2. Extract it and make sure these paths exist inside the plugin folder:")
    for rel in failed:
        print(f"     {rel}")
    print(f"  3. Re-run this script with --check to verify.\n")
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="only verify that all runtime files are correctly installed",
    )
    parser.add_argument(
        "--no-lfs",
        action="store_true",
        help="skip `git lfs pull` and use direct HTTPS download only",
    )
    args = parser.parse_args()

    if not args.check and not args.no_lfs:
        try_git_lfs_pull()

    failed: list[str] = []
    for item in MANIFEST:
        if not ensure_one(item, check_only=args.check, allow_https=not args.no_lfs):
            failed.append(item["rel"])

    if args.check:
        print(
            f"\nCheck complete: {len(MANIFEST) - len(failed)}/{len(MANIFEST)} files OK."
        )
        return 0 if not failed else 1

    if failed:
        manual_instructions(failed)
        return 1
    print("\nAll large runtime files are in place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
