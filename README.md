# ComfyUI NVIDIA DLSS 5 Visual Enhancer

Native NVIDIA DLSS processing for ComfyUI with three separate nodes:

- **NVIDIA DLSS Frame Interpolation** — increases video frame rate.
- **NVIDIA DLSS Video Upscale** — increases video resolution.
- **NVIDIA DLSS Image Upscale** — increases image or image-batch resolution.

The video nodes accept and return ComfyUI's native `VIDEO` type. They do not create permanent output: connect their output to ComfyUI's built-in **Save Video** node. The image node accepts and returns an `IMAGE` tensor directly without creating an image file.

```text
Load Video -> NVIDIA DLSS Frame Interpolation -> Save Video
Load Video -> NVIDIA DLSS Video Upscale       -> Save Video
Load Image -> NVIDIA DLSS Image Upscale       -> Save Image
```

## Example comparison

This five-second sample combines **3x video upscaling** with **frame interpolation to 120 FPS**.

The sample media (MP4, GIF, PNG) is not stored in this repository. It is
distributed together with the large runtime files — download it from the
[Hugging Face repository](https://huggingface.co/dashengAi/ComfyUI-NV-DLSS-Frame-dashengAi-runtime)
or the cloud-drive bundle (see [Large files](#large-files--大文件)).

| Metric | Original | Processed | Change |
| --- | ---: | ---: | ---: |
| Resolution | 1024×640 | 3072×1920 | 3x width and height |
| Pixels per frame | 655,360 | 5,898,240 | 9x |
| Nominal frame rate | 24 FPS | 120 FPS | 5x |
| Frames | 121 | 606 | Approximately 5x |
| Duration | 5.024 seconds | 5.050 seconds | Preserved within one output-frame interval |

### Processing time for this sample

Benchmark system:

| Component | Specification |
| --- | --- |
| GPU | NVIDIA GeForce RTX 4060 Ti, 16 GB VRAM (16,380 MiB reported) |
| NVIDIA driver | 610.62 |
| CPU | AMD Ryzen 5 7600, 6 cores / 12 threads, 3.8 GHz base clock |
| System RAM | 64 GB (63.1 GiB reported by Windows) |
| Operating system | Windows 11 Enterprise 64-bit, version 10.0.26200 |

| Stage | Key settings | Time |
| --- | --- | ---: |
| Video upscale | 3x Ultra Performance, Natural NR style, Max quality, H.264 NVENC | 12.670 seconds |
| Frame interpolation | 120 FPS, Cascade engine, Max quality, H.264 NVENC | 153.747 seconds |
| **Combined processing** | Upscale followed by interpolation | **166.417 seconds (2m 46.417s)** |

These are ComfyUI node execution times for the approximately five-second sample on the benchmark system above—about 33.1x the source duration in total. They include decoding, optical-flow guide generation, DLSS processing, encoding, and muxing. Performance varies with the GPU, CPU, source resolution, target FPS, codec, and selected quality settings; these timings are not real-time playback FPS.

The animated comparison is a deliberately slowed demonstration, not real-time playback. Use the linked MP4 files to inspect the actual resolution and frame rate.

## Features

- Native ComfyUI `VIDEO` and `IMAGE` connections
- Bundled DLSS Frame Generation, Super Resolution, and Neural Rendering runtimes
- Separate image-upscale and video-upscale nodes
- DLSS resolution modes from native/DLAA through 3x Ultra Performance
- NR preset, style, intensity, tone, structure, skin structure, automatic mask, and DLSS model controls
- Native or cascaded frame interpolation with exact fractional FPS choices
- CPU and NVIDIA NVENC video encoders
- MP4, MKV, and MOV temporary video containers
- Optional 10-bit HDR video path with compatible codecs
- Original video audio, supported subtitles, chapters, and metadata preserved where the selected container allows
- JSON diagnostics from every node
- No model downloads or telemetry; the only network request is the one-time runtime-bundle fetch from Hugging Face on first install

## Requirements

- Windows, or Linux x86-64 with Wine (experimental; see below)
- A compatible NVIDIA RTX GPU
- A current NVIDIA display driver
- A current ComfyUI installation
- FFmpeg and FFprobe available on `PATH`

No extra Python packages are required beyond the packages included with current ComfyUI (`torch`, `av`, `numpy`, and OpenCV). The NVIDIA DLLs, RenoDX/ReShade carrier, and native workers are fetched automatically on first install from Hugging Face (or restored from the cloud-drive bundle); see [Installation](#installation). FFmpeg and FFprobe are the only intentionally external runtime tools.

Hardware-accelerated GPU scheduling (HAGS) is recommended for Frame Generation on Windows. Capability is checked at runtime; GPU branding alone does not guarantee that every DLSS path will initialize.

### Linux

Use the [Linux setup helper](docs/linux.md#setup-helper-recommended) to prepare
a dedicated Wine prefix, save worker settings, and optionally verify all
three nodes. The [Linux guide](docs/linux.md) also includes manual setup. ComfyUI,
PyTorch, and FFmpeg run natively; only the bundled Windows workers run through
Wine, DXVK, VKD3D-Proton, and DXVK-NVAPI. After setup, start ComfyUI normally;
the saved settings apply only to Linux workers. Manual `WINEPREFIX` and
`DLSS_WINE_PATH` environment configuration is also supported.

All three nodes have been tested on an RTX 5090 with driver 610.57.04 and
GE-Proton 11-6: native/cascaded frame interpolation, image-batch upscaling,
and video upscaling with audio preservation and confirmed NR execution.
NR requires the included ReShade descriptor fix, native shader compiler,
and Wine heap settings described in the guide.

The tested upscale modes used DLSS SR followed by native-resolution NR;
the runtime reported `nr_native_fallback: true`. Direct NR upscaling was
not active, and **Require Neural Upscaling** still rejects that fallback.

## Installation

This repository stays under 50 MB: the large NVIDIA runtime binaries
(~234 MB) are **not** stored in GitHub. They are tracked with Git LFS and
hosted on [Hugging Face](https://huggingface.co/dashengAi/ComfyUI-NV-DLSS-Frame-dashengAi-runtime);
a cloud-drive bundle is the manual fallback (see [Large files](#large-files--大文件)).

1. Clone the repository (ComfyUI-Manager or manually):

   ```bash
   git clone https://github.com/<your-github>/ComfyUI-NV-DLSS-Frame-dashengAi.git
   ```

   Place the folder at:

   ```text
   ComfyUI/custom_nodes/ComfyUI-NV-DLSS-Frame-dashengAi/
   ```

2. Fetch the large runtime files. This happens **automatically in the
   background on the first ComfyUI start** (requires git-lfs and network
   access to Hugging Face). To fetch them right away, run:

   ```bash
   git lfs pull
   # or, without git-lfs:
   python scripts/download_large_files.py
   ```

3. Confirm that FFmpeg and FFprobe are available:

   ```powershell
   ffmpeg -version
   ffprobe -version
   ```

4. Restart ComfyUI.

If FFmpeg is not on `PATH`, define both variables before starting ComfyUI:

```powershell
$env:DLSS_FFMPEG_PATH = "C:\path\to\ffmpeg.exe"
$env:DLSS_FFPROBE_PATH = "C:\path\to\ffprobe.exe"
```

### Important for source clones

A fresh clone contains tiny Git LFS pointer files (a few hundred bytes)
instead of the real DLLs. Before the pointers are resolved the plugin is
incomplete. The plugin checks on startup and fetches them automatically; you
can also run `git lfs pull` (uses the Hugging Face endpoint configured in
`.lfsconfig`) or `python scripts/download_large_files.py` manually.

## Large files（大文件）

| Component | Location |
| --- | --- |
| Large runtime binaries (6 files, ~233 MB) | `bin/runtime/...`, tracked with Git LFS, objects hosted on Hugging Face |
| Small runtime binaries (`nvngx.dll`, `dlssg-worker.exe`) | committed directly in this repository |
| Local staging folder (8 files, ~234 MB, gitignored) | `large_files/` — used for uploading to Hugging Face / cloud drive |
| Downloader script | `scripts/download_large_files.py` |

- **Automatic (network to Hugging Face works):** `git lfs pull` on startup /
  `python scripts/download_large_files.py`.
- **Manual (Hugging Face unreachable):** download the cloud-drive bundle
  from <https://pan.quark.cn/s/c65a50478105> and extract it so that
  `bin/runtime/host/...`, `bin/runtime/dlss/...`, `bin/runtime/dlssg/...`,
  `bin/runtime/host-linux/...` are present, then verify with
  `python scripts/download_large_files.py --check`.
- Mainland-China mirror for the Hugging Face endpoint: replace
  `huggingface.co` with `hf-mirror.com` in `.lfsconfig`
  (`https://hf-mirror.com/dashengAi/ComfyUI-NV-DLSS-Frame-dashengAi-runtime.git/info/lfs`).

## NVIDIA DLSS Video Upscale

This node accepts a `VIDEO`, processes every frame, preserves the source timing, and returns a temporary upscaled `VIDEO` suitable for **Save Video**.

### Upscale modes

| Mode | Output dimensions |
| --- | --- |
| `1x (DLAA / native)` | Native dimensions; enhancement/anti-aliasing path |
| `1.5x (Quality)` | Width and height multiplied by 1.5 |
| `1.724x (Balanced)` | Width and height multiplied by 1.724 |
| `2x (Performance)` | Width and height multiplied by 2 |
| `3x (Ultra Performance)` | Width and height multiplied by 3 |

Calculated output dimensions are rounded to even values. The maximum supported boundary is 7680×4320, including portrait orientation.

The video node also exposes encoding quality, codec, container, temporary rename/suffix, and HDR controls. Connect `upscaled_video` to **Save Video** to choose the permanent destination.

## NVIDIA DLSS Image Upscale

This node accepts a ComfyUI `IMAGE` batch and returns the upscaled batch directly in memory. It uses the same resolution and Neural Rendering controls as the video-upscale node but has no codec or filename options because it does not encode or save files.

- RGB input returns RGB output.
- RGBA input returns RGBA output with the source alpha resized using Lanczos filtering.
- Single-channel input is converted to RGB output.
- Each input dimension must be at least 64 pixels.

Connect `upscaled_image` to **Preview Image**, **Save Image**, or another image-processing node.

## Neural Rendering controls

Both upscale nodes expose the controls retained from DLSS 5 Visual Enhancer:

| Option | Choices/range |
| --- | --- |
| Require Neural Upscaling | On/off; when on, rejects fallback output if NVIDIA reports neural upscaling inactive |
| NR Preset | `Default`, `Preset #1`, `Preset #2`, `Preset #3` |
| NR Style | `Default`, `Natural`, `Cinematic` |
| NR Intensity | `0.0`–`2.0` |
| Local Tone Strength | `0.0`–`2.0` |
| Local Structure Strength | `0.0`–`2.0` |
| Skin Structure Strength | `-1.0`–`2.0` |
| Automatic Mask | On/off |
| DLSS Model Preset | `Default`, `J`, `K`, `L`, `M` |

The `report_json` output records the requested controls, negotiated render size, final output size, GPU, feature-18 evidence, and whether the neural upscaling path was active.

### Neural-upscaling status matters

A larger output does not automatically prove that DLSS neural upscaling reconstructed additional detail. Check these report fields:

- `feature_18_confirmed`: the signed DLSS Neural Rendering feature executed.
- `nr_upscaling_active`: the feature accepted and used the low-resolution upscaling contract.
- `nr_native_fallback`: the runtime rejected that contract and continued through its native fallback path.

If `nr_upscaling_active` is `false`, the output still has the requested larger dimensions, but it must not be described as confirmed neural Super Resolution. Results depend on source dimensions, driver, GPU, and the bundled runtime.

Enable **Require Neural Upscaling** when a workflow must never continue with fallback output. It does not force an unsupported NVIDIA path to activate; it turns the runtime result into a strict pass/fail requirement.

## NVIDIA DLSS Frame Interpolation

This node creates intermediate frames and returns a higher-FPS `VIDEO`.

| Option | Choices | Behavior |
| --- | --- | --- |
| Output FPS | `23.976`, `25`, `29.97`, `30`, `50`, `59.94`, `60`, `90`, `120` | Fractional choices use exact 1001-based rates. The target may not exceed 6x the source rate. |
| DLSS engine | `Auto`, `Native DLSSG`, `Cascade` | Auto uses a supported exact native grid and otherwise cascades 2x stages. |
| Encoding quality | `Auto (Default)`, `Max`, `Best`, `Good` | Controls the temporary encoded `VIDEO`. |
| Video codec | H.264, H.265, AV1, ProRes Proxy, and NVENC variants | Plain choices use CPU encoding; suffixed choices use NVIDIA NVENC. |
| Container | `MP4`, `MKV`, `MOV` | ProRes Proxy requires MOV or MKV. |
| Rename | `Auto`, `Copy`, `Custom` | Applies only to the temporary intermediate. |
| HDR Mode | On/off | 10-bit output for H.265, AV1, or ProRes Proxy. |

**Auto** uses native DLSSG when the source and target rates form an exact multiplier supported by the installed runtime. Otherwise it creates a deterministic cascaded grid and selects frames on the exact requested timeline. Scene cuts reset interpolation history.

## Temporary video behavior

Video processing needs an encoded intermediate because ComfyUI's lazy `VIDEO` object requires a streamable source. The nodes write only below ComfyUI's temporary directory:

```text
ComfyUI/temp/dlssfg-output-*/
ComfyUI/temp/dlss5-video-output-*/
```

Nothing is written to `ComfyUI/output` by these processing nodes. ComfyUI may clear temporary files during normal cleanup or restart. Use **Save Video** in the same workflow whenever the result must persist.

Native workers and FFmpeg processes launch without visible console windows. Incomplete outputs are removed on failure, and ComfyUI cancellation interrupts processing.

## Troubleshooting

### Nodes do not appear

Make sure `README.md` and `__init__.py` are directly inside the custom-node folder, then restart ComfyUI and inspect its startup console for an import error.

### Runtime DLL is missing or only a few bytes

The Git LFS objects were not downloaded (the clone still contains pointer
files). Run `git lfs pull`, or `python scripts/download_large_files.py`, or
restore from the cloud-drive bundle (see [Large files](#large-files--大文件)).

### FFmpeg or FFprobe was not found

Install FFmpeg and place both executables on `PATH`, or set `DLSS_FFMPEG_PATH` and `DLSS_FFPROBE_PATH` before starting ComfyUI.

### Frame Generation is unavailable

Update the NVIDIA driver, enable HAGS in Windows graphics settings, restart Windows, and retry. `report_json` includes the detected GPU, driver, runtime, signature status, and supported native multiplier.

On Linux, HAGS is not applicable. Check the Wine/NGX setup and run the
capability probe in [docs/linux.md](docs/linux.md#verify-frame-interpolation).

### Native DLSSG rejects the requested FPS

Choose **Auto** or **Cascade**. Native mode accepts only an exact constant-frame-rate multiplier supported by the runtime.

### HDR input is rejected

Enable HDR Mode and select H.265, AV1, or ProRes Proxy. H.264 is SDR-only.

## Validation

The packaged folder was validated with portable ComfyUI on Windows and an RTX 4060 Ti:

- All three V3 nodes loaded and registered.
- A real `64×64` IMAGE produced a `96×96` IMAGE tensor using Quality mode.
- A trimmed `640×512` VIDEO produced a `960×768` temporary VIDEO using Quality mode, with AAC audio retained.
- Frame interpolation was re-run after the shared runtime integration and produced a valid temporary output.
- Video outputs were confirmed below `ComfyUI/temp`, not `ComfyUI/output`.
- In the tested upscale runs, feature 18 was confirmed but neural upscaling reported inactive with native fallback. This is surfaced in `report_json` rather than hidden.

This is validation of the tested local configuration, not a guarantee for every GPU, driver, source format, resolution, codec, or HDR input.

## Credits and licenses

The implementation is derived from [DLSS 5 Visual Enhancer](https://github.com/Merserk/dlss5-visual-enhancer) v5.0 by Merserk. Its MIT license is included as [`LICENSE-DLSS-Visual-Enhancer.txt`](LICENSE-DLSS-Visual-Enhancer.txt).

Runtime licenses are included beside their respective files under `bin/runtime/host`, `bin/runtime/dlss`, and `bin/runtime/dlssg`. Review the NVIDIA DLSS license before redistribution or commercial release; it includes a commercial-release notification requirement for applications incorporating the DLSS SDK.

RenoDX and ReShade license texts are included under `bin/runtime/host`.

FFmpeg and FFprobe are not included and remain governed by the license of the user's installation.

### SDR output detail strength

The image and video upscale nodes expose optional **output_detail_strength**
(default **1.0**, maximum **2.0**). One leaves the worker output byte-for-byte
unchanged, including existing workflows. Two amplifies brightness differences
between the source and rendered output using a bounded luminance ratio. This
is an output adjustment, separate from **nr_intensity**, and does not run the
neural model again or prove stronger neural synthesis.

Start with **Cinematic**, **nr_intensity = 2**, and **output_detail_strength = 2**
to reproduce the stronger composition setting explored in the Linux tests.
Compare against strength 1 on the same clip. The adjustment runs on the CPU
using NumPy, in small row tiles; DLSS/NR inference remains on the NVIDIA GPU.
It preserves alpha and video timing/audio. With upscaling, the reference is
resized to the output size, so the adjustment includes SR changes as well as NR.
HDR mode requires strength 1; this composition has only been validated for SDR.

Reports include `output_composition` with the strength, method and execution
location. Feature-18 evidence describes model execution, not visual parity with
Windows. The control implements SDR brightness-ratio amplification; it is not
a bundled OptiScaler backend or its full game exposure/color pipeline. The
investigation used [OptiScaler's shader](https://github.com/Dagherbou/OptiScaler_DLSSNR/blob/dlss-neural-rendering/OptiScaler/shaders/dlssnr/precompile/dlssnr.hlsl)
as a separate GPU reference, and no GPL shader or binary is redistributed here.
