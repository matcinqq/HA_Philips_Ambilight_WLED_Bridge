from __future__ import annotations

import dataclasses
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - exercised only in incomplete environments
    yaml = None

from .models import ConfigError
from .transforms import DEFAULT_PEAK_COMPRESSION_CURVE

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True, slots=True)
class TVConfig:
    host: str | None = None
    port: int = 1926
    api_version: int = 6
    username: str | None = None
    password: str | None = None
    verify_tls: bool = False
    timeout_seconds: float = 1.0


@dataclass(frozen=True, slots=True)
class WLEDConfig:
    host: str | None = None
    timeout_seconds: float = 1.0
    normal_preset_id: int = 1
    ambilight_preset_id: int = 2
    live_transition: int = 0
    suppress_udp_sync: bool = True
    use_white_channel: bool = True


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    update_interval_ms: int = 100
    brightness_multiplier: float = 1.0
    red_gain: float = 1.0
    green_gain: float = 1.0
    blue_gain: float = 1.0
    saturation: float = 1.0
    white_mix: float = 0.0
    white_mix_strategy: str = "balanced"
    color_correction_matrix: tuple[tuple[float, float, float], ...] = field(
        default_factory=lambda: (
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        )
    )
    smoothing_alpha: float = 0.20
    max_channel_step: int = 0
    channel_deadband: int = 0
    send_threshold: int = 0
    restore_normal_on_exit: bool = True
    restore_normal_after_tv_loss_seconds: float | None = 30.0
    reconnect_backoff_seconds: float = 1.0
    preset_settle_seconds: float = 0.25
    timing_log_interval_seconds: float = 5.0
    white_extraction: str = "none"
    white_gain: float = 1.0
    rgbw_tint_gain: float = 1.0
    white_cool_blue_boost: float = 0.0
    black_floor_white: bool = False
    black_floor_white_level: int = 5
    black_floor_threshold: int = 8
    max_brightness: int = 255


@dataclass(frozen=True, slots=True)
class TimingConfig:
    philips_poll_interval_ms: int = 50
    wled_render_interval_ms: int = 33


@dataclass(frozen=True, slots=True)
class SmoothingConfig:
    enabled: bool = True
    time_constant_ms: float = 120.0


@dataclass(frozen=True, slots=True)
class OutputConfig:
    backend: str = "json_segments"


@dataclass(frozen=True, slots=True)
class DDPConfig:
    host: str | None = None
    port: int = 4048
    pixel_count: int = 86


@dataclass(frozen=True, slots=True)
class IntensityCompressionConfig:
    enabled: bool = False
    method: str = "peak"
    strength: float = 1.0
    curve: tuple[tuple[float, float], ...] = DEFAULT_PEAK_COMPRESSION_CURVE


@dataclass(frozen=True, slots=True)
class ZoneOrder:
    lower: str
    middle: str
    upper: str


@dataclass(frozen=True, slots=True)
class BottomBlendConfig:
    center_left_t: float = 0.33
    center_right_t: float = 0.67


@dataclass(frozen=True, slots=True)
class MappingConfig:
    left: ZoneOrder = field(default_factory=lambda: ZoneOrder(lower="0", middle="1", upper="2"))
    right: ZoneOrder = field(default_factory=lambda: ZoneOrder(lower="2", middle="1", upper="0"))
    bottom_blend: BottomBlendConfig = field(default_factory=BottomBlendConfig)


@dataclass(frozen=True, slots=True)
class SegmentIds:
    left_lower: int = 0
    left_middle: int = 1
    left_upper: int = 2
    right_lower: int = 3
    right_middle: int = 4
    right_upper: int = 5
    bottom_outer_left: int = 6
    bottom_center_left: int = 7
    bottom_center_right: int = 8
    bottom_outer_right: int = 9


@dataclass(frozen=True, slots=True)
class AppConfig:
    tv: TVConfig = field(default_factory=TVConfig)
    wled: WLEDConfig = field(default_factory=WLEDConfig)
    bridge: BridgeConfig = field(default_factory=BridgeConfig)
    timing: TimingConfig = field(default_factory=TimingConfig)
    smoothing: SmoothingConfig = field(default_factory=SmoothingConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    ddp: DDPConfig = field(default_factory=DDPConfig)
    intensity_compression: IntensityCompressionConfig = field(default_factory=IntensityCompressionConfig)
    mapping: MappingConfig = field(default_factory=MappingConfig)
    segment_ids: SegmentIds = field(default_factory=SegmentIds)

    def validate(self, require_tv_credentials: bool = True) -> None:
        if require_tv_credentials:
            if not self.tv.host:
                raise ConfigError("tv.host is required")
            if not (1 <= self.tv.port <= 65535):
                raise ConfigError("tv.port must be in the range 1..65535")
            if self.tv.api_version <= 0:
                raise ConfigError("tv.api_version must be positive")
            if self.tv.timeout_seconds <= 0:
                raise ConfigError("tv.timeout_seconds must be positive")
            if not self.tv.username or not self.tv.password:
                raise ConfigError("Philips TV username/password are required")

        if not self.wled.host:
            raise ConfigError("wled.host is required")
        if self.wled.timeout_seconds <= 0:
            raise ConfigError("wled.timeout_seconds must be positive")
        if self.wled.normal_preset_id <= 0:
            raise ConfigError("wled.normal_preset_id must be positive")
        if self.wled.ambilight_preset_id <= 0:
            raise ConfigError("wled.ambilight_preset_id must be positive")
        if self.wled.live_transition < 0:
            raise ConfigError("wled.live_transition must not be negative")

        if self.bridge.update_interval_ms < 50:
            raise ConfigError("bridge.update_interval_ms must be at least 50 for WLED JSON API")
        if self.bridge.brightness_multiplier < 0:
            raise ConfigError("bridge.brightness_multiplier must not be negative")
        for gain_name, gain_value in (
            ("bridge.red_gain", self.bridge.red_gain),
            ("bridge.green_gain", self.bridge.green_gain),
            ("bridge.blue_gain", self.bridge.blue_gain),
            ("bridge.white_gain", self.bridge.white_gain),
            ("bridge.rgbw_tint_gain", self.bridge.rgbw_tint_gain),
            ("bridge.white_cool_blue_boost", self.bridge.white_cool_blue_boost),
        ):
            if gain_value < 0:
                raise ConfigError(f"{gain_name} must not be negative")
        if not isinstance(self.bridge.black_floor_white, bool):
            raise ConfigError("bridge.black_floor_white must be true or false")
        if (
            isinstance(self.bridge.black_floor_white_level, bool)
            or not isinstance(self.bridge.black_floor_white_level, int)
            or not (0 <= self.bridge.black_floor_white_level <= 255)
        ):
            raise ConfigError("bridge.black_floor_white_level must be between 0 and 255")
        if (
            isinstance(self.bridge.black_floor_threshold, bool)
            or not isinstance(self.bridge.black_floor_threshold, int)
            or not (0 <= self.bridge.black_floor_threshold <= 255)
        ):
            raise ConfigError("bridge.black_floor_threshold must be between 0 and 255")
        if (
            isinstance(self.bridge.max_brightness, bool)
            or not isinstance(self.bridge.max_brightness, int)
            or not (0 <= self.bridge.max_brightness <= 255)
        ):
            raise ConfigError("bridge.max_brightness must be between 0 and 255")
        if not (0.0 <= self.bridge.saturation <= 2.0):
            raise ConfigError("bridge.saturation must be between 0 and 2")
        if not (0.0 <= self.bridge.white_mix <= 1.0):
            raise ConfigError("bridge.white_mix must be between 0 and 1")
        if self.bridge.white_mix_strategy not in {"adaptive", "balanced", "always"}:
            raise ConfigError("bridge.white_mix_strategy must be 'adaptive', 'balanced', or 'always'")
        _validate_color_matrix(self.bridge.color_correction_matrix)
        if self.bridge.white_extraction not in {"none", "min_rgb"}:
            raise ConfigError("bridge.white_extraction must be 'none' or 'min_rgb'")
        if self.bridge.white_extraction != "none" and not self.wled.use_white_channel:
            raise ConfigError("wled.use_white_channel must be true when bridge.white_extraction is enabled")
        if not (0.0 < self.bridge.smoothing_alpha <= 1.0):
            raise ConfigError("bridge.smoothing_alpha must be > 0 and <= 1")
        if self.bridge.max_channel_step < 0:
            raise ConfigError("bridge.max_channel_step must not be negative")
        if self.bridge.channel_deadband < 0:
            raise ConfigError("bridge.channel_deadband must not be negative")
        if self.bridge.send_threshold < 0:
            raise ConfigError("bridge.send_threshold must not be negative")
        if self.bridge.reconnect_backoff_seconds <= 0:
            raise ConfigError("bridge.reconnect_backoff_seconds must be positive")
        if self.bridge.preset_settle_seconds < 0:
            raise ConfigError("bridge.preset_settle_seconds must not be negative")
        if self.bridge.timing_log_interval_seconds < 0:
            raise ConfigError("bridge.timing_log_interval_seconds must not be negative")
        if self.timing.philips_poll_interval_ms <= 0:
            raise ConfigError("timing.philips_poll_interval_ms must be positive")
        if self.timing.wled_render_interval_ms <= 0:
            raise ConfigError("timing.wled_render_interval_ms must be positive")
        if not isinstance(self.smoothing.enabled, bool):
            raise ConfigError("smoothing.enabled must be true or false")
        if self.smoothing.time_constant_ms < 0:
            raise ConfigError("smoothing.time_constant_ms must not be negative")
        if self.output.backend not in {"json_segments", "ddp_pixels"}:
            raise ConfigError("output.backend must be 'json_segments' or 'ddp_pixels'")
        if (
            isinstance(self.ddp.port, bool)
            or not isinstance(self.ddp.port, int)
            or not (1 <= self.ddp.port <= 65535)
        ):
            raise ConfigError("ddp.port must be in the range 1..65535")
        if isinstance(self.ddp.pixel_count, bool) or not isinstance(self.ddp.pixel_count, int) or self.ddp.pixel_count <= 0:
            raise ConfigError("ddp.pixel_count must be positive")
        if self.output.backend == "ddp_pixels":
            if not (self.ddp.host or self.wled.host):
                raise ConfigError("ddp.host or wled.host is required when output.backend is 'ddp_pixels'")
            if self.ddp.pixel_count != 86:
                raise ConfigError("ddp.pixel_count must be 86 for the configured physical layout")
        if not isinstance(self.intensity_compression.enabled, bool):
            raise ConfigError("intensity_compression.enabled must be true or false")
        if self.intensity_compression.method != "peak":
            raise ConfigError("intensity_compression.method must be 'peak'")
        if not (0.0 <= self.intensity_compression.strength <= 1.0):
            raise ConfigError("intensity_compression.strength must be between 0 and 1")
        _validate_curve("intensity_compression.curve", self.intensity_compression.curve)

        for name, blend_t in (
            ("mapping.bottom_blend.center_left_t", self.mapping.bottom_blend.center_left_t),
            ("mapping.bottom_blend.center_right_t", self.mapping.bottom_blend.center_right_t),
        ):
            if not (0.0 <= blend_t <= 1.0):
                raise ConfigError(f"{name} must be between 0 and 1")

        segment_fields = dataclasses.fields(self.segment_ids)
        segment_values = [getattr(self.segment_ids, field.name) for field in segment_fields]
        if any(value < 0 for value in segment_values):
            raise ConfigError("segment IDs must not be negative")
        if len(set(segment_values)) != len(segment_fields):
            raise ConfigError("segment IDs must be unique")


def load_dotenv(path: str | Path = ".env") -> None:
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_config(path: str | Path | None = None) -> AppConfig:
    data: dict[str, Any] = {}
    if path is not None:
        if yaml is None:
            raise ConfigError(
                "PyYAML is required to read YAML config files. "
                'Install project dependencies with: python3 -m pip install -e "."'
            )
        config_path = Path(path)
        if not config_path.exists():
            raise ConfigError(f"Config file not found: {config_path}")
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ConfigError("Config root must be a mapping")
        data = _expand_env(raw)

    return _app_config_from_dict(data)


def _expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, str):
        return ENV_PATTERN.sub(lambda match: os.environ.get(match.group(1), ""), value)
    return value


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    section = data.get(name, {})
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ConfigError(f"{name} must be a mapping")
    return section


def _app_config_from_dict(data: dict[str, Any]) -> AppConfig:
    tv_data = {
        "host": os.environ.get("PHILIPS_TV_HOST"),
        "username": os.environ.get("PHILIPS_TV_USER"),
        "password": os.environ.get("PHILIPS_TV_PASS"),
    } | _section(data, "tv")
    wled_data = {
        "host": os.environ.get("WLED_HOST"),
    } | _section(data, "wled")
    bridge_data = _section(data, "bridge")
    timing_data = _section(data, "timing")
    smoothing_data = _section(data, "smoothing")
    output_data = _section(data, "output")
    ddp_data = {
        "host": os.environ.get("WLED_HOST") or os.environ.get("WLED_IP"),
    } | _section(data, "ddp")
    compression_data = _section(data, "intensity_compression")
    mapping_data = _section(data, "mapping")
    segment_data = _section(data, "segment_ids")

    left_data = _section(mapping_data, "left") if mapping_data else {}
    right_data = _section(mapping_data, "right") if mapping_data else {}
    bottom_data = _section(mapping_data, "bottom_blend") if mapping_data else {}

    if "color_correction_matrix" in bridge_data:
        bridge_data = bridge_data | {
            "color_correction_matrix": _parse_color_matrix(bridge_data["color_correction_matrix"])
        }
    if "curve" in compression_data:
        compression_data = compression_data | {
            "curve": _parse_curve(compression_data["curve"], "intensity_compression.curve")
        }

    try:
        return AppConfig(
            tv=TVConfig(**tv_data),
            wled=WLEDConfig(**wled_data),
            bridge=BridgeConfig(**bridge_data),
            timing=TimingConfig(**timing_data),
            smoothing=SmoothingConfig(**smoothing_data),
            output=OutputConfig(**output_data),
            ddp=DDPConfig(**ddp_data),
            intensity_compression=IntensityCompressionConfig(**compression_data),
            mapping=MappingConfig(
                left=ZoneOrder(**({"lower": "0", "middle": "1", "upper": "2"} | left_data)),
                right=ZoneOrder(**({"lower": "2", "middle": "1", "upper": "0"} | right_data)),
                bottom_blend=BottomBlendConfig(**bottom_data),
            ),
            segment_ids=SegmentIds(**segment_data),
        )
    except TypeError as exc:
        raise ConfigError(f"Invalid configuration key or value: {exc}") from exc


def _parse_color_matrix(value: object) -> tuple[tuple[float, float, float], ...]:
    if not isinstance(value, list | tuple) or len(value) != 3:
        raise ConfigError("bridge.color_correction_matrix must be a 3x3 numeric matrix")

    rows: list[tuple[float, float, float]] = []
    for row in value:
        if not isinstance(row, list | tuple) or len(row) != 3:
            raise ConfigError("bridge.color_correction_matrix must be a 3x3 numeric matrix")
        try:
            rows.append((float(row[0]), float(row[1]), float(row[2])))
        except (TypeError, ValueError) as exc:
            raise ConfigError("bridge.color_correction_matrix must be numeric") from exc
    matrix = tuple(rows)
    _validate_color_matrix(matrix)
    return matrix


def _validate_color_matrix(matrix: object) -> None:
    if not isinstance(matrix, tuple) or len(matrix) != 3:
        raise ConfigError("bridge.color_correction_matrix must be a 3x3 numeric matrix")

    for row in matrix:
        if not isinstance(row, tuple) or len(row) != 3:
            raise ConfigError("bridge.color_correction_matrix must be a 3x3 numeric matrix")
        for value in row:
            if not isinstance(value, int | float):
                raise ConfigError("bridge.color_correction_matrix must be numeric")
            if not (-10.0 <= float(value) <= 10.0):
                raise ConfigError("bridge.color_correction_matrix values must be between -10 and 10")


def _parse_curve(value: object, name: str) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list | tuple):
        raise ConfigError(f"{name} must be a list of [input, output] points")

    points: list[tuple[float, float]] = []
    for point in value:
        if not isinstance(point, list | tuple) or len(point) != 2:
            raise ConfigError(f"{name} must be a list of [input, output] points")
        try:
            points.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{name} points must be numeric") from exc

    curve = tuple(points)
    _validate_curve(name, curve)
    return curve


def _validate_curve(name: str, curve: object) -> None:
    if not isinstance(curve, tuple) or len(curve) < 2:
        raise ConfigError(f"{name} must contain at least two points")

    previous_input: float | None = None
    for point in curve:
        if not isinstance(point, tuple) or len(point) != 2:
            raise ConfigError(f"{name} must be a list of [input, output] points")
        input_value, output_value = point
        if not isinstance(input_value, int | float) or not isinstance(output_value, int | float):
            raise ConfigError(f"{name} points must be numeric")
        if not (0.0 <= float(input_value) <= 255.0) or not (0.0 <= float(output_value) <= 255.0):
            raise ConfigError(f"{name} points must be between 0 and 255")
        if previous_input is not None and float(input_value) <= previous_input:
            raise ConfigError(f"{name} input points must be strictly increasing")
        previous_input = float(input_value)
