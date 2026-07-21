from __future__ import annotations

import colorsys
from dataclasses import dataclass
from math import cos, pi

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

# (input hue degrees, hue shift degrees, saturation scale, value scale)
PHILIPS_MATCH_HUE_PROFILE: tuple[tuple[float, float, float, float], ...] = (
    (0.0, 5.58, 0.9556, 0.8860),
    (18.43, 4.01, 1.0000, 1.0000),
    (55.51, 0.00, 1.0000, 1.0000),
    (120.0, 0.00, 1.0000, 1.0000),
    (186.85, -37.66, 0.7255, 1.0040),
    (239.53, -7.57, 0.9573, 0.9212),
    (320.08, 10.08, 0.7878, 0.9647),
    (360.0, 5.58, 0.9556, 0.8860),
)


@dataclass(frozen=True, slots=True)
class PeakCompressionResult:
    input: FloatRGB
    output: FloatRGB
    peak: float
    compressed_peak: float
    scale: float
    enabled: bool


def apply_philips_match_profile(color: ColorValue) -> FloatRGB:
    source = FloatRGB.from_color(color)
    red = _clamp(source.r) / 255.0
    green = _clamp(source.g) / 255.0
    blue = _clamp(source.b) / 255.0
    if max(red, green, blue) <= 0.0:
        return FloatRGB(0.0, 0.0, 0.0, source.w)

    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
    hue_degrees = hue * 360.0
    hue_shift, saturation_scale, value_scale = _interpolate_hue_profile(hue_degrees)

    dark_weight = _smoothstep(0.35, 0.05, value)
    warm_weight = _warm_hue_weight(hue_degrees)
    dark_warm_weight = dark_weight * warm_weight
    hue_shift += 15.2 * dark_warm_weight
    saturation_scale += 0.019 * dark_warm_weight
    value_scale += 0.101 * dark_warm_weight

    chromatic = colorsys.hsv_to_rgb(
        ((hue_degrees + hue_shift) % 360.0) / 360.0,
        _clamp_unit(saturation * saturation_scale),
        _clamp_unit(value * value_scale),
    )
    neutral = (
        _clamp_unit(red * (255.0 / 254.0)),
        _clamp_unit(green * (255.0 / 254.0)),
        _clamp_unit(blue * (170.0 / 254.0)),
    )
    chromatic_weight = _smoothstep(0.15, 0.65, saturation)
    return FloatRGB(
        _blend(neutral[0], chromatic[0], chromatic_weight) * 255.0,
        _blend(neutral[1], chromatic[1], chromatic_weight) * 255.0,
        _blend(neutral[2], chromatic[2], chromatic_weight) * 255.0,
        source.w,
    )


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


def _interpolate_hue_profile(hue_degrees: float) -> tuple[float, float, float]:
    previous = PHILIPS_MATCH_HUE_PROFILE[0]
    for current in PHILIPS_MATCH_HUE_PROFILE[1:]:
        if hue_degrees <= current[0]:
            t = (hue_degrees - previous[0]) / (current[0] - previous[0])
            return (
                _blend(previous[1], current[1], t),
                _blend(previous[2], current[2], t),
                _blend(previous[3], current[3], t),
            )
        previous = current
    return previous[1], previous[2], previous[3]


def _warm_hue_weight(hue_degrees: float) -> float:
    distance = abs(((hue_degrees - 18.0 + 180.0) % 360.0) - 180.0)
    if distance >= 38.0:
        return 0.0
    return 0.5 + 0.5 * cos(pi * distance / 38.0)


def _smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge0 == edge1:
        return 0.0
    t = _clamp_unit((value - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def _blend(start: float, end: float, amount: float) -> float:
    return start + (end - start) * amount


def _clamp(value: float) -> float:
    return max(0.0, min(255.0, value))


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))
