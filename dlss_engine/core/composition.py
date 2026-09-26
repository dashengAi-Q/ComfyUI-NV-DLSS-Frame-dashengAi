"""Bounded SDR luminance amplification after neural rendering.

This is output composition, not an additional neural inference pass. Keeping
it outside the worker protocol preserves compatibility with Windows workers.
"""

import math

import cv2
import numpy as np


def validate_detail_strength(value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or not 1.0 <= value <= 2.0:
        raise ValueError("Output detail strength must be between 1 and 2.")
    return value


def composition_report(strength: float) -> dict:
    return {
        "output_detail_strength": strength,
        "applied": strength != 1.0,
        "method": "bounded-sdr-luminance-ratio",
        "execution": "cpu" if strength != 1.0 else "bypass",
        "maximum_luminance_ratio": 2.0,
    }


def compose_sdr(
    original: np.ndarray, rendered: np.ndarray, strength: float = 1.0
) -> np.ndarray:
    """Amplify the rendered luminance change without extrapolating RGB.

One is an exact bypass. Above one, raise the rendered/original luminance
ratio to the requested power, bound it to [1/2, 2], and scale rendered RGB
uniformly to preserve its hue before final 8-bit clipping. A small shared
luminance floor avoids unstable division in shadows. Work in row tiles to
bound temporary memory for 4K video; alpha is never part of composition.

Inputs are display-referred uint8 RGBA frames. A resized source provides a
reference when SR and NR have already produced a larger output; this is not
an isolated NR residual in that case.
    """
    strength = validate_detail_strength(strength)
    if strength == 1.0:
        return rendered
    for frame in (original, rendered):
        if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 4:
            raise ValueError("SDR composition requires uint8 RGBA frames.")
    height, width = rendered.shape[:2]
    if original.shape[:2] != (height, width):
        original = cv2.resize(
            original, (width, height), interpolation=cv2.INTER_LINEAR
        )
    result = rendered.copy()
    weights = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    floor = np.float32(1.0 / 512.0)
    for row in range(0, height, 64):
        end = min(row + 64, height)
        source = original[row:end, :, :3].astype(np.float32) / 255.0
        model = rendered[row:end, :, :3].astype(np.float32) / 255.0
        source_luma = source @ weights
        model_luma = model @ weights
        ratio = (model_luma + floor) / (source_luma + floor)
        bounded = np.clip(ratio ** strength, 0.5, 2.0)
        enhanced = model * (bounded / ratio)[..., None]
        # An empty model answer is not usable detail; retain the source.
        enhanced = np.where((model_luma <= 1e-5)[..., None], source, enhanced)
        result[row:end, :, :3] = np.rint(
            np.clip(enhanced, 0.0, 1.0) * 255.0
        ).astype(np.uint8)
    return result
