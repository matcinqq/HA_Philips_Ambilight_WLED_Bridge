from __future__ import annotations

import math

from .models import ColorValue, FloatRGB


def smoothing_alpha(dt_seconds: float, tau_seconds: float) -> float:
    if tau_seconds <= 0:
        return 1.0
    if dt_seconds <= 0:
        return 0.0
    return 1.0 - math.exp(-dt_seconds / tau_seconds)


class TimeBasedSegmentSmoother:
    def __init__(self, enabled: bool = True, time_constant_seconds: float = 0.120) -> None:
        if time_constant_seconds < 0:
            raise ValueError("time_constant_seconds must not be negative")
        self.enabled = enabled
        self.time_constant_seconds = time_constant_seconds
        self._display: dict[int, FloatRGB] | None = None

    @property
    def display(self) -> dict[int, FloatRGB] | None:
        if self._display is None:
            return None
        return dict(self._display)

    def reset(self) -> None:
        self._display = None

    def smooth(self, target: dict[int, ColorValue], dt_seconds: float) -> tuple[dict[int, FloatRGB], float]:
        target_float = {
            segment_id: FloatRGB.from_color(color).force_white(0.0)
            for segment_id, color in target.items()
        }
        if self._display is None or set(self._display) != set(target_float):
            self._display = dict(target_float)
            return dict(self._display), 1.0

        alpha = 1.0 if not self.enabled else smoothing_alpha(dt_seconds, self.time_constant_seconds)
        self._display = {
            segment_id: _smooth_color(self._display[segment_id], target_color, alpha)
            for segment_id, target_color in target_float.items()
        }
        return dict(self._display), alpha


class SegmentSmoother:
    def __init__(self, alpha: float, max_channel_step: int = 0, channel_deadband: int = 0) -> None:
        if not (0.0 < alpha <= 1.0):
            raise ValueError("alpha must be > 0 and <= 1")
        if max_channel_step < 0:
            raise ValueError("max_channel_step must not be negative")
        if channel_deadband < 0:
            raise ValueError("channel_deadband must not be negative")
        self.alpha = alpha
        self.max_channel_step = max_channel_step
        self.channel_deadband = channel_deadband
        self._previous: dict[int, FloatRGB] = {}

    def smooth(self, colors: dict[int, ColorValue]) -> dict[int, FloatRGB]:
        smoothed: dict[int, FloatRGB] = {}
        for segment_id, color in colors.items():
            current = FloatRGB.from_color(color)
            previous = self._previous.get(segment_id)
            if previous is None:
                smoothed[segment_id] = current.force_white(0.0)
                continue

            target = previous.blend(current.force_white(0.0), self.alpha)
            target = _apply_deadband(previous, target, self.channel_deadband)
            smoothed[segment_id] = _limit_step(previous, target, self.max_channel_step)
        self._previous = dict(smoothed)
        return smoothed

    def reset(self) -> None:
        self._previous.clear()


def colors_changed(
    current: dict[int, ColorValue],
    previous: dict[int, ColorValue] | None,
    threshold: int = 0,
) -> bool:
    if previous is None:
        return True
    if set(current) != set(previous):
        return True
    if threshold <= 0:
        return any(
            current[key].as_list(include_white=True) != previous[key].as_list(include_white=True)
            for key in current
        )
    return any(FloatRGB.from_color(current[key]).max_channel_delta(previous[key]) > threshold for key in current)


def _limit_step(previous: FloatRGB, target: FloatRGB, max_step: int) -> FloatRGB:
    if max_step <= 0:
        return target
    return FloatRGB(
        _limit_channel(previous.r, target.r, max_step),
        _limit_channel(previous.g, target.g, max_step),
        _limit_channel(previous.b, target.b, max_step),
        _limit_channel(previous.w, target.w, max_step),
    )


def _limit_channel(previous: float, target: float, max_step: int) -> float:
    delta = target - previous
    if delta > max_step:
        return previous + max_step
    if delta < -max_step:
        return previous - max_step
    return target


def _apply_deadband(previous: FloatRGB, target: FloatRGB, deadband: int) -> FloatRGB:
    if deadband <= 0:
        return target
    return FloatRGB(
        _deadband_channel(previous.r, target.r, deadband),
        _deadband_channel(previous.g, target.g, deadband),
        _deadband_channel(previous.b, target.b, deadband),
        _deadband_channel(previous.w, target.w, deadband),
    )


def _deadband_channel(previous: float, target: float, deadband: int) -> float:
    if abs(target - previous) <= deadband:
        return previous
    return target


def _smooth_color(previous: FloatRGB, target: FloatRGB, alpha: float) -> FloatRGB:
    return FloatRGB(
        previous.r + alpha * (target.r - previous.r),
        previous.g + alpha * (target.g - previous.g),
        previous.b + alpha * (target.b - previous.b),
        0.0,
    )
