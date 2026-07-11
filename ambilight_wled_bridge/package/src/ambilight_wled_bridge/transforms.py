from __future__ import annotations

from dataclasses import dataclass

from .models import ColorValue, FloatRGB

DEFAULT_PEAK_COMPRESSION_CURVE: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (5.0, 5.0),
    (10.0, 10.0),
    (20.0, 19.0),
    (40.0, 32.0),
    (60.0, 42.0),
    (80.0, 52.0),
    (100.0, 62.0),
    (150.0, 90.0),
    (200.0, 125.0),
    (255.0, 165.0),
)


@dataclass(frozen=True, slots=True)
class PeakCompressionResult:
    input: FloatRGB
    output: FloatRGB
    peak: float
    compressed_peak: float
    scale: float
    enabled: bool


def compress_peak_intensity(
    color: ColorValue,
    *,
    enabled: bool,
    strength: float = 1.0,
    curve: tuple[tuple[float, float], ...] = DEFAULT_PEAK_COMPRESSION_CURVE,
) -> FloatRGB:
    return trace_peak_intensity_compression(
        color,
        enabled=enabled,
        strength=strength,
        curve=curve,
    ).output


def trace_peak_intensity_compression(
    color: ColorValue,
    *,
    enabled: bool,
    strength: float = 1.0,
    curve: tuple[tuple[float, float], ...] = DEFAULT_PEAK_COMPRESSION_CURVE,
) -> PeakCompressionResult:
    input_color = FloatRGB.from_color(color)
    peak = max(input_color.r, input_color.g, input_color.b)
    if not enabled or peak <= 0.0:
        return PeakCompressionResult(
            input=input_color,
            output=input_color,
            peak=peak,
            compressed_peak=peak,
            scale=1.0,
            enabled=enabled,
        )

    target_peak = _interpolate_curve(peak, curve)
    effective_peak = peak + (target_peak - peak) * strength
    scale = effective_peak / peak
    return PeakCompressionResult(
        input=input_color,
        output=FloatRGB(input_color.r * scale, input_color.g * scale, input_color.b * scale, input_color.w),
        peak=peak,
        compressed_peak=effective_peak,
        scale=scale,
        enabled=enabled,
    )


def _interpolate_curve(value: float, curve: tuple[tuple[float, float], ...]) -> float:
    points = tuple(sorted(curve))
    if not points:
        return value
    if value <= points[0][0]:
        return points[0][1]

    previous_x, previous_y = points[0]
    for current_x, current_y in points[1:]:
        if value <= current_x:
            if current_x == previous_x:
                return current_y
            t = (value - previous_x) / (current_x - previous_x)
            return previous_y + (current_y - previous_y) * t
        previous_x, previous_y = current_x, current_y

    return points[-1][1]
