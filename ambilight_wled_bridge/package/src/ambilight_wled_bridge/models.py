from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Protocol


class BridgeError(Exception):
    """Base class for controlled bridge errors."""


class ConfigError(BridgeError):
    """Invalid or incomplete configuration."""


class PhilipsAPIError(BridgeError):
    """Philips JointSpace API request or response error."""


class WLEDAPIError(BridgeError):
    """WLED JSON API request or response error."""


class MappingError(BridgeError):
    """Malformed Ambilight payload or invalid mapping configuration."""


def clamp_channel(value: float | int) -> int:
    return max(0, min(255, int(floor(float(value) + 0.5))))


class ColorValue(Protocol):
    r: float
    g: float
    b: float
    w: float

    def as_list(self, include_white: bool = False) -> list[int]:
        ...


@dataclass(frozen=True, slots=True)
class RGB:
    r: int
    g: int
    b: int
    w: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "r", clamp_channel(self.r))
        object.__setattr__(self, "g", clamp_channel(self.g))
        object.__setattr__(self, "b", clamp_channel(self.b))
        object.__setattr__(self, "w", clamp_channel(self.w))

    @classmethod
    def from_mapping(cls, value: object, path: str) -> "RGB":
        if not isinstance(value, dict):
            raise MappingError(f"{path} must be an object with r/g/b values")

        missing = [channel for channel in ("r", "g", "b") if channel not in value]
        if missing:
            raise MappingError(f"{path} missing channel(s): {', '.join(missing)}")

        try:
            return cls(int(value["r"]), int(value["g"]), int(value["b"]))
        except (TypeError, ValueError) as exc:
            raise MappingError(f"{path} channels must be integers") from exc

    def as_list(self, include_white: bool = False) -> list[int]:
        if include_white:
            return [self.r, self.g, self.b, self.w]
        return [self.r, self.g, self.b]

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.r, self.g, self.b)

    def apply_gain(self, gain: float) -> "RGB":
        return self.apply_gains(gain, 1.0, 1.0, 1.0)

    def apply_gains(
        self,
        brightness_gain: float,
        red_gain: float = 1.0,
        green_gain: float = 1.0,
        blue_gain: float = 1.0,
    ) -> "RGB":
        return RGB(
            clamp_channel(self.r * brightness_gain * red_gain),
            clamp_channel(self.g * brightness_gain * green_gain),
            clamp_channel(self.b * brightness_gain * blue_gain),
        )

    def apply_matrix(self, matrix: tuple[tuple[float, float, float], ...]) -> "RGB":
        return RGB(
            self.r * matrix[0][0] + self.g * matrix[0][1] + self.b * matrix[0][2],
            self.r * matrix[1][0] + self.g * matrix[1][1] + self.b * matrix[1][2],
            self.r * matrix[2][0] + self.g * matrix[2][1] + self.b * matrix[2][2],
        )

    def apply_matrix_and_gains(
        self,
        matrix: tuple[tuple[float, float, float], ...],
        brightness_gain: float,
        red_gain: float = 1.0,
        green_gain: float = 1.0,
        blue_gain: float = 1.0,
    ) -> "RGB":
        red = self.r * matrix[0][0] + self.g * matrix[0][1] + self.b * matrix[0][2]
        green = self.r * matrix[1][0] + self.g * matrix[1][1] + self.b * matrix[1][2]
        blue = self.r * matrix[2][0] + self.g * matrix[2][1] + self.b * matrix[2][2]
        return RGB(
            red * brightness_gain * red_gain,
            green * brightness_gain * green_gain,
            blue * brightness_gain * blue_gain,
        )

    def blend(self, other: "RGB", t: float) -> "RGB":
        return RGB(
            clamp_channel(self.r * (1.0 - t) + other.r * t),
            clamp_channel(self.g * (1.0 - t) + other.g * t),
            clamp_channel(self.b * (1.0 - t) + other.b * t),
            clamp_channel(self.w * (1.0 - t) + other.w * t),
        )

    def max_channel_delta(self, other: "RGB") -> int:
        return max(
            abs(self.r - other.r),
            abs(self.g - other.g),
            abs(self.b - other.b),
            abs(self.w - other.w),
        )

    def apply_saturation(self, saturation: float) -> "RGB":
        if saturation == 1.0:
            return self
        luma = self.r * 0.2126 + self.g * 0.7152 + self.b * 0.0722
        return RGB(
            luma + (self.r - luma) * saturation,
            luma + (self.g - luma) * saturation,
            luma + (self.b - luma) * saturation,
            self.w,
        )

    def extract_min_rgb_white(self, white_gain: float = 1.0) -> "RGB":
        white_base = min(self.r, self.g, self.b)
        return RGB(
            self.r - white_base,
            self.g - white_base,
            self.b - white_base,
            white_base * white_gain,
        )

    def scale_rgb(self, gain: float) -> "RGB":
        return RGB(self.r * gain, self.g * gain, self.b * gain, self.w)

    def add_cool_blue_from_white(self, gain: float) -> "RGB":
        if gain <= 0:
            return self
        return RGB(self.r, self.g, self.b + self.w * gain, self.w)

    def mix_toward_white(self, amount: float, strategy: str = "adaptive") -> "RGB":
        if amount <= 0:
            return self
        neutral = max(self.r, self.g, self.b)
        if neutral <= 0:
            return self
        effective_amount = amount
        if strategy == "adaptive":
            middle = sorted((self.r, self.g, self.b))[1]
            effective_amount *= middle / neutral
        elif strategy == "balanced":
            effective_amount *= min(self.r, self.g, self.b) / neutral
        return RGB(
            self.r * (1.0 - effective_amount) + neutral * effective_amount,
            self.g * (1.0 - effective_amount) + neutral * effective_amount,
            self.b * (1.0 - effective_amount) + neutral * effective_amount,
            self.w,
        )

    def is_black(self) -> bool:
        return self.r == 0 and self.g == 0 and self.b == 0 and self.w == 0

    def with_white_floor(self, level: int, use_white_channel: bool) -> "RGB":
        if use_white_channel:
            return RGB(self.r, self.g, self.b, max(self.w, level))
        return RGB(max(self.r, level), max(self.g, level), max(self.b, level), self.w)

    def limit_channels(self, max_value: int) -> "RGB":
        if max_value >= 255:
            return self
        return RGB(
            min(self.r, max_value),
            min(self.g, max_value),
            min(self.b, max_value),
            min(self.w, max_value),
        )

    def to_float(self) -> "FloatRGB":
        return FloatRGB(float(self.r), float(self.g), float(self.b), float(self.w))


@dataclass(frozen=True, slots=True)
class FloatRGB:
    r: float
    g: float
    b: float
    w: float = 0.0

    @classmethod
    def from_color(cls, color: ColorValue) -> "FloatRGB":
        return cls(float(color.r), float(color.g), float(color.b), float(color.w))

    @classmethod
    def from_mapping(cls, value: object, path: str) -> "FloatRGB":
        return RGB.from_mapping(value, path).to_float()

    def as_rgb(self) -> RGB:
        return RGB(self.r, self.g, self.b, self.w)

    def as_list(self, include_white: bool = False) -> list[int]:
        return self.as_rgb().as_list(include_white)

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.r, self.g, self.b)

    def apply_matrix_and_gains(
        self,
        matrix: tuple[tuple[float, float, float], ...],
        brightness_gain: float,
        red_gain: float = 1.0,
        green_gain: float = 1.0,
        blue_gain: float = 1.0,
    ) -> "FloatRGB":
        red = self.r * matrix[0][0] + self.g * matrix[0][1] + self.b * matrix[0][2]
        green = self.r * matrix[1][0] + self.g * matrix[1][1] + self.b * matrix[1][2]
        blue = self.r * matrix[2][0] + self.g * matrix[2][1] + self.b * matrix[2][2]
        return FloatRGB(
            red * brightness_gain * red_gain,
            green * brightness_gain * green_gain,
            blue * brightness_gain * blue_gain,
            self.w,
        )

    def blend(self, other: ColorValue, t: float) -> "FloatRGB":
        other_color = FloatRGB.from_color(other)
        return FloatRGB(
            self.r * (1.0 - t) + other_color.r * t,
            self.g * (1.0 - t) + other_color.g * t,
            self.b * (1.0 - t) + other_color.b * t,
            self.w * (1.0 - t) + other_color.w * t,
        )

    def max_channel_delta(self, other: ColorValue) -> float:
        other_color = FloatRGB.from_color(other)
        return max(
            abs(self.r - other_color.r),
            abs(self.g - other_color.g),
            abs(self.b - other_color.b),
            abs(self.w - other_color.w),
        )

    def apply_saturation(self, saturation: float) -> "FloatRGB":
        if saturation == 1.0:
            return self
        luma = self.r * 0.2126 + self.g * 0.7152 + self.b * 0.0722
        return FloatRGB(
            luma + (self.r - luma) * saturation,
            luma + (self.g - luma) * saturation,
            luma + (self.b - luma) * saturation,
            self.w,
        )

    def extract_min_rgb_white(self, white_gain: float = 1.0) -> "FloatRGB":
        white_base = min(self.r, self.g, self.b)
        return FloatRGB(
            self.r - white_base,
            self.g - white_base,
            self.b - white_base,
            white_base * white_gain,
        )

    def scale_rgb(self, gain: float) -> "FloatRGB":
        return FloatRGB(self.r * gain, self.g * gain, self.b * gain, self.w)

    def add_cool_blue_from_white(self, gain: float) -> "FloatRGB":
        if gain <= 0:
            return self
        return FloatRGB(self.r, self.g, self.b + self.w * gain, self.w)

    def mix_toward_white(self, amount: float, strategy: str = "adaptive") -> "FloatRGB":
        if amount <= 0:
            return self
        neutral = max(self.r, self.g, self.b)
        if neutral <= 0:
            return self
        effective_amount = amount
        if strategy == "adaptive":
            middle = sorted((self.r, self.g, self.b))[1]
            effective_amount *= middle / neutral
        elif strategy == "balanced":
            effective_amount *= min(self.r, self.g, self.b) / neutral
        return FloatRGB(
            self.r * (1.0 - effective_amount) + neutral * effective_amount,
            self.g * (1.0 - effective_amount) + neutral * effective_amount,
            self.b * (1.0 - effective_amount) + neutral * effective_amount,
            self.w,
        )

    def is_black(self) -> bool:
        return self.r == 0 and self.g == 0 and self.b == 0 and self.w == 0

    def with_white_floor(self, level: int, use_white_channel: bool) -> "FloatRGB":
        if use_white_channel:
            return FloatRGB(self.r, self.g, self.b, max(self.w, float(level)))
        return FloatRGB(max(self.r, float(level)), max(self.g, float(level)), max(self.b, float(level)), self.w)

    def limit_channels(self, max_value: int) -> "FloatRGB":
        if max_value >= 255:
            return self
        max_float = float(max_value)
        return FloatRGB(
            min(self.r, max_float),
            min(self.g, max_float),
            min(self.b, max_float),
            min(self.w, max_float),
        )

    def force_white(self, value: float) -> "FloatRGB":
        return FloatRGB(self.r, self.g, self.b, value)
