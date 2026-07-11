from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .config import AppConfig
from .ddp import DDPClient
from .models import ColorValue, ConfigError, FloatRGB
from .wled import WLEDClient

DDP_PIXEL_COUNT = 86

PIXEL_SEGMENT_RANGES: dict[int, range] = {
    0: range(21, 30),
    1: range(30, 38),
    2: range(38, 47),
    3: range(72, 77),
    4: range(77, 82),
    5: range(82, 86),
    6: range(10, 21),
    7: range(0, 10),
    8: range(47, 59),
    9: range(59, 72),
}


class OutputBackend(Protocol):
    backend_name: str

    @property
    def packets_sent(self) -> int:
        ...

    @property
    def send_failures(self) -> int:
        ...

    def start(self) -> None:
        ...

    def send_segments(self, segments: dict[int, ColorValue]) -> None:
        ...

    def stop(self) -> None:
        ...


class JsonSegmentsBackend:
    backend_name = "json_segments"

    def __init__(self, wled: WLEDClient) -> None:
        self.wled = wled
        self._packets_sent = 0
        self._send_failures = 0

    @property
    def packets_sent(self) -> int:
        return self._packets_sent

    @property
    def send_failures(self) -> int:
        return self._send_failures

    def start(self) -> None:
        return None

    def send_segments(self, segments: dict[int, ColorValue]) -> None:
        try:
            self.wled.update_segment_colors(segments)
        except Exception:
            self._send_failures += 1
            raise
        self._packets_sent += 1

    def stop(self) -> None:
        return None


class DDPixelsBackend:
    backend_name = "ddp_pixels"

    def __init__(self, ddp: DDPClient, pixel_count: int = DDP_PIXEL_COUNT) -> None:
        self.ddp = ddp
        self.pixel_count = pixel_count

    @property
    def packets_sent(self) -> int:
        return self.ddp.packets_sent

    @property
    def send_failures(self) -> int:
        return self.ddp.send_failures

    def start(self) -> None:
        self.ddp.start()

    def send_segments(self, segments: dict[int, ColorValue]) -> None:
        self.ddp.send_pixels(expand_segments_to_pixels(segments, self.pixel_count))

    def stop(self) -> None:
        self.ddp.stop()


def build_output_backend(config: AppConfig, wled: WLEDClient) -> OutputBackend:
    if config.output.backend == "json_segments":
        return JsonSegmentsBackend(wled)
    if config.output.backend == "ddp_pixels":
        host = config.ddp.host or config.wled.host
        if not host:
            raise ConfigError("ddp.host or wled.host is required for output.backend=ddp_pixels")
        return DDPixelsBackend(DDPClient(host, config.ddp.port), config.ddp.pixel_count)
    raise ConfigError(f"Unknown output backend: {config.output.backend}")


def expand_segments_to_pixels(
    segments: dict[int, ColorValue],
    pixel_count: int = DDP_PIXEL_COUNT,
) -> list[FloatRGB]:
    _validate_pixel_layout(pixel_count)
    missing = sorted(set(PIXEL_SEGMENT_RANGES) - set(segments))
    if missing:
        raise ConfigError(f"Missing segment color(s) for DDP pixel expansion: {missing}")

    pixels: list[FloatRGB | None] = [None] * pixel_count
    for segment_id, pixel_range in PIXEL_SEGMENT_RANGES.items():
        color = FloatRGB.from_color(segments[segment_id]).force_white(0.0)
        for pixel_index in pixel_range:
            pixels[pixel_index] = color

    if any(pixel is None for pixel in pixels):  # pragma: no cover - covered by layout tests
        raise ConfigError("DDP pixel layout left at least one pixel unassigned")
    return [pixel for pixel in pixels if pixel is not None]


def covered_pixel_indices(pixel_count: int = DDP_PIXEL_COUNT) -> list[int]:
    _validate_pixel_layout(pixel_count)
    indices: list[int] = []
    for pixel_range in PIXEL_SEGMENT_RANGES.values():
        indices.extend(pixel_range)
    return indices


def describe_pixel_ranges() -> list[tuple[int, int, int]]:
    return [(segment_id, pixel_range.start, pixel_range.stop - 1) for segment_id, pixel_range in sorted(PIXEL_SEGMENT_RANGES.items())]


def _validate_pixel_layout(pixel_count: int) -> None:
    if pixel_count != DDP_PIXEL_COUNT:
        raise ConfigError(f"ddp.pixel_count must be {DDP_PIXEL_COUNT} for the configured physical layout")
    indices = [index for pixel_range in PIXEL_SEGMENT_RANGES.values() for index in pixel_range]
    if sorted(indices) != list(range(pixel_count)):
        raise ConfigError("DDP pixel layout must cover every physical pixel exactly once")
