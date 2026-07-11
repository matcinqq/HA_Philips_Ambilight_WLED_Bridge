from __future__ import annotations

from dataclasses import dataclass

from .config import MappingConfig, SegmentIds, ZoneOrder
from .models import ColorValue, FloatRGB, MappingError, RGB


@dataclass(frozen=True, slots=True)
class LogicalColors:
    left_lower: RGB
    left_middle: RGB
    left_upper: RGB
    right_lower: RGB
    right_middle: RGB
    right_upper: RGB
    bottom_outer_left: RGB
    bottom_center_left: RGB
    bottom_center_right: RGB
    bottom_outer_right: RGB


@dataclass(frozen=True, slots=True)
class FloatLogicalColors:
    left_lower: FloatRGB
    left_middle: FloatRGB
    left_upper: FloatRGB
    right_lower: FloatRGB
    right_middle: FloatRGB
    right_upper: FloatRGB
    bottom_outer_left: FloatRGB
    bottom_center_left: FloatRGB
    bottom_center_right: FloatRGB
    bottom_outer_right: FloatRGB


@dataclass(frozen=True, slots=True)
class ColorStage:
    name: str
    color: FloatRGB | None
    detail: str = ""


LOGICAL_COLOR_NAMES = (
    "left_lower",
    "left_middle",
    "left_upper",
    "right_lower",
    "right_middle",
    "right_upper",
    "bottom_outer_left",
    "bottom_center_left",
    "bottom_center_right",
    "bottom_outer_right",
)


class AmbilightMapper:
    def __init__(
        self,
        mapping: MappingConfig | None = None,
        segment_ids: SegmentIds | None = None,
        brightness_multiplier: float = 1.0,
        red_gain: float = 1.0,
        green_gain: float = 1.0,
        blue_gain: float = 1.0,
        color_correction_matrix: tuple[tuple[float, float, float], ...] | None = None,
        saturation: float = 1.0,
        white_mix: float = 0.0,
        white_mix_strategy: str = "adaptive",
        white_extraction: str = "none",
        white_gain: float = 1.0,
        rgbw_tint_gain: float = 1.0,
        white_cool_blue_boost: float = 0.0,
        black_floor_white: bool = False,
        black_floor_white_level: int = 5,
        black_floor_threshold: int = 8,
        max_brightness: int = 255,
        use_white_channel: bool = False,
    ) -> None:
        self.mapping = mapping or MappingConfig()
        self.segment_ids = segment_ids or SegmentIds()
        self.brightness_multiplier = brightness_multiplier
        self.red_gain = red_gain
        self.green_gain = green_gain
        self.blue_gain = blue_gain
        self.saturation = saturation
        self.white_mix = white_mix
        self.white_mix_strategy = white_mix_strategy
        self.white_extraction = white_extraction
        self.white_gain = white_gain
        self.rgbw_tint_gain = rgbw_tint_gain
        self.white_cool_blue_boost = white_cool_blue_boost
        self.black_floor_white = black_floor_white
        self.black_floor_white_level = black_floor_white_level
        self.black_floor_threshold = black_floor_threshold
        self.max_brightness = max_brightness
        self.use_white_channel = use_white_channel
        self.color_correction_matrix = color_correction_matrix or (
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        )

    def map_processed(self, payload: object) -> dict[int, RGB]:
        return {segment_id: color.as_rgb() for segment_id, color in self.map_processed_float(payload).items()}

    def map_processed_float(self, payload: object) -> dict[int, FloatRGB]:
        colors = self.map_logical_float(payload)
        return self.logical_to_segments_float(colors)

    def map_raw_segments(self, payload: object) -> dict[int, RGB]:
        return {segment_id: color.as_rgb() for segment_id, color in self.map_raw_segments_float(payload).items()}

    def map_raw_segments_float(self, payload: object) -> dict[int, FloatRGB]:
        colors = self.map_logical_raw_float(payload)
        return self.logical_to_segments_float(colors)

    def logical_to_segments(self, colors: LogicalColors) -> dict[int, RGB]:
        return {
            self.segment_ids.left_lower: colors.left_lower,
            self.segment_ids.left_middle: colors.left_middle,
            self.segment_ids.left_upper: colors.left_upper,
            self.segment_ids.right_lower: colors.right_lower,
            self.segment_ids.right_middle: colors.right_middle,
            self.segment_ids.right_upper: colors.right_upper,
            self.segment_ids.bottom_outer_left: colors.bottom_outer_left,
            self.segment_ids.bottom_center_left: colors.bottom_center_left,
            self.segment_ids.bottom_center_right: colors.bottom_center_right,
            self.segment_ids.bottom_outer_right: colors.bottom_outer_right,
        }

    def logical_to_segments_float(self, colors: FloatLogicalColors) -> dict[int, FloatRGB]:
        return {
            self.segment_ids.left_lower: colors.left_lower,
            self.segment_ids.left_middle: colors.left_middle,
            self.segment_ids.left_upper: colors.left_upper,
            self.segment_ids.right_lower: colors.right_lower,
            self.segment_ids.right_middle: colors.right_middle,
            self.segment_ids.right_upper: colors.right_upper,
            self.segment_ids.bottom_outer_left: colors.bottom_outer_left,
            self.segment_ids.bottom_center_left: colors.bottom_center_left,
            self.segment_ids.bottom_center_right: colors.bottom_center_right,
            self.segment_ids.bottom_outer_right: colors.bottom_outer_right,
        }

    def map_logical(self, payload: object) -> LogicalColors:
        colors = self.map_logical_float(payload)
        return LogicalColors(
            left_lower=colors.left_lower.as_rgb(),
            left_middle=colors.left_middle.as_rgb(),
            left_upper=colors.left_upper.as_rgb(),
            right_lower=colors.right_lower.as_rgb(),
            right_middle=colors.right_middle.as_rgb(),
            right_upper=colors.right_upper.as_rgb(),
            bottom_outer_left=colors.bottom_outer_left.as_rgb(),
            bottom_center_left=colors.bottom_center_left.as_rgb(),
            bottom_center_right=colors.bottom_center_right.as_rgb(),
            bottom_outer_right=colors.bottom_outer_right.as_rgb(),
        )

    def map_logical_float(self, payload: object) -> FloatLogicalColors:
        raw = self.map_logical_raw_float(payload)
        return FloatLogicalColors(
            left_lower=self._process_color(raw.left_lower),
            left_middle=self._process_color(raw.left_middle),
            left_upper=self._process_color(raw.left_upper),
            right_lower=self._process_color(raw.right_lower),
            right_middle=self._process_color(raw.right_middle),
            right_upper=self._process_color(raw.right_upper),
            bottom_outer_left=self._process_color(raw.bottom_outer_left),
            bottom_center_left=self._process_color(raw.bottom_center_left),
            bottom_center_right=self._process_color(raw.bottom_center_right),
            bottom_outer_right=self._process_color(raw.bottom_outer_right),
        )

    def map_logical_raw(self, payload: object) -> LogicalColors:
        raw = self.map_logical_raw_float(payload)
        return LogicalColors(
            left_lower=raw.left_lower.as_rgb(),
            left_middle=raw.left_middle.as_rgb(),
            left_upper=raw.left_upper.as_rgb(),
            right_lower=raw.right_lower.as_rgb(),
            right_middle=raw.right_middle.as_rgb(),
            right_upper=raw.right_upper.as_rgb(),
            bottom_outer_left=raw.bottom_outer_left.as_rgb(),
            bottom_center_left=raw.bottom_center_left.as_rgb(),
            bottom_center_right=raw.bottom_center_right.as_rgb(),
            bottom_outer_right=raw.bottom_outer_right.as_rgb(),
        )

    def map_logical_raw_float(self, payload: object) -> FloatLogicalColors:
        layer = _require_mapping(payload, "payload").get("layer1")
        layer_map = _require_mapping(layer, "payload.layer1")
        left = _require_mapping(layer_map.get("left"), "payload.layer1.left")
        right = _require_mapping(layer_map.get("right"), "payload.layer1.right")

        left_lower = self._read_ordered_zone(left, self.mapping.left, "lower", "left", process=False)
        left_middle = self._read_ordered_zone(left, self.mapping.left, "middle", "left", process=False)
        left_upper = self._read_ordered_zone(left, self.mapping.left, "upper", "left", process=False)
        right_lower = self._read_ordered_zone(right, self.mapping.right, "lower", "right", process=False)
        right_middle = self._read_ordered_zone(right, self.mapping.right, "middle", "right", process=False)
        right_upper = self._read_ordered_zone(right, self.mapping.right, "upper", "right", process=False)

        bottom_outer_left = left_lower
        bottom_center_left = left_lower.blend(
            right_lower,
            self.mapping.bottom_blend.center_left_t,
        )
        bottom_center_right = left_lower.blend(
            right_lower,
            self.mapping.bottom_blend.center_right_t,
        )
        bottom_outer_right = right_lower

        return FloatLogicalColors(
            left_lower=left_lower,
            left_middle=left_middle,
            left_upper=left_upper,
            right_lower=right_lower,
            right_middle=right_middle,
            right_upper=right_upper,
            bottom_outer_left=bottom_outer_left,
            bottom_center_left=bottom_center_left,
            bottom_center_right=bottom_center_right,
            bottom_outer_right=bottom_outer_right,
        )

    def _read_ordered_zone(
        self,
        side_payload: dict[str, object],
        order: ZoneOrder,
        position: str,
        side_name: str,
        process: bool = True,
    ) -> FloatRGB:
        zone_id = getattr(order, position)
        path = f"payload.layer1.{side_name}.{zone_id}"
        if zone_id not in side_payload:
            raise MappingError(f"{path} is missing")
        color = FloatRGB.from_mapping(side_payload[zone_id], path)
        if not process:
            return color
        return self._process_color(color)

    def _process_color(self, color: ColorValue) -> FloatRGB:
        final = self.trace_color(color)[-1].color
        if final is None:  # pragma: no cover - final stage always has a color
            raise MappingError("color trace ended without a color")
        return final

    def trace_color(self, color: ColorValue) -> list[ColorStage]:
        current = FloatRGB.from_color(color)
        stages = [ColorStage("philips_raw", current)]

        current = current.apply_matrix_and_gains(
            self.color_correction_matrix,
            self.brightness_multiplier,
            self.red_gain,
            self.green_gain,
            self.blue_gain,
        )
        stages.append(
            ColorStage(
                "after_matrix_gain_brightness",
                current,
                (
                    f"brightness={self.brightness_multiplier}, "
                    f"gains=({self.red_gain},{self.green_gain},{self.blue_gain})"
                ),
            )
        )

        if self.saturation == 1.0:
            stages.append(ColorStage("after_saturation", current, "disabled"))
        else:
            current = current.apply_saturation(self.saturation)
            stages.append(ColorStage("after_saturation", current, f"saturation={self.saturation}"))

        if self.white_mix <= 0:
            stages.append(ColorStage("after_white_mix", current, "disabled"))
        else:
            current = current.mix_toward_white(self.white_mix, self.white_mix_strategy)
            stages.append(
                ColorStage(
                    "after_white_mix",
                    current,
                    f"amount={self.white_mix}, strategy={self.white_mix_strategy}",
                )
            )

        stages.append(ColorStage("rgbw_conversion_input", current))
        if self.white_extraction == "min_rgb":
            current = (
                current.extract_min_rgb_white(self.white_gain)
                .scale_rgb(self.rgbw_tint_gain)
                .add_cool_blue_from_white(self.white_cool_blue_boost)
            )
            stages.append(
                ColorStage(
                    "rgbw_conversion_output",
                    current,
                    (
                        "white_extraction=min_rgb, "
                        f"white_gain={self.white_gain}, "
                        f"rgbw_tint_gain={self.rgbw_tint_gain}, "
                        f"white_cool_blue_boost={self.white_cool_blue_boost}"
                    ),
                )
            )
        else:
            stages.append(ColorStage("rgbw_conversion_output", current, "disabled"))

        if self.black_floor_white and _is_near_black_but_not_black(current, self.black_floor_threshold):
            current = current.with_white_floor(self.black_floor_white_level, self.use_white_channel)
            stages.append(
                ColorStage(
                    "after_black_floor",
                    current,
                    f"level={self.black_floor_white_level}, threshold={self.black_floor_threshold}",
                )
            )
        else:
            stages.append(ColorStage("after_black_floor", current, "disabled"))

        if self.max_brightness >= 255:
            stages.append(ColorStage("after_max_brightness", current, "disabled"))
        else:
            current = current.limit_channels(self.max_brightness)
            stages.append(ColorStage("after_max_brightness", current, f"max={self.max_brightness}"))
        return stages


def logical_color_items(colors: LogicalColors | FloatLogicalColors) -> list[tuple[str, RGB | FloatRGB]]:
    return [(name, getattr(colors, name)) for name in LOGICAL_COLOR_NAMES]


def _require_mapping(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise MappingError(f"{path} must be an object")
    return value


def _is_near_black_but_not_black(color: RGB, threshold: int) -> bool:
    if color.is_black() or threshold <= 0:
        return False
    return max(color.r, color.g, color.b, color.w) <= threshold
