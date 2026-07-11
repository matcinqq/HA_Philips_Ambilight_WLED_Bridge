from __future__ import annotations

import logging
import time
from dataclasses import dataclass, fields
from threading import Event, Lock, Thread

from .config import AppConfig
from .mapping import AmbilightMapper
from .models import FloatRGB, MappingError, PhilipsAPIError, WLEDAPIError
from .output import OutputBackend, build_output_backend
from .philips import PhilipsClient
from .smoothing import TimeBasedSegmentSmoother
from .transforms import compress_peak_intensity
from .wled import WLEDClient

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class TimingStats:
    tv_polls: int = 0
    tv_poll_interval_total: float = 0.0
    tv_request_latency_total: float = 0.0
    tv_failures: int = 0
    wled_renders: int = 0
    wled_render_interval_total: float = 0.0
    wled_request_latency_total: float = 0.0
    wled_failures: int = 0
    last_tv_poll_started: float | None = None
    last_wled_render_started: float | None = None


class AmbilightBridge:
    def __init__(
        self,
        config: AppConfig,
        philips: PhilipsClient | None = None,
        wled: WLEDClient | None = None,
        output_backend: OutputBackend | None = None,
    ) -> None:
        self.config = config
        self.philips = philips or PhilipsClient(config.tv)
        self.wled = wled or WLEDClient(config.wled)
        self.output = output_backend or build_output_backend(config, self.wled)
        self.mapper = AmbilightMapper(
            mapping=config.mapping,
            segment_ids=config.segment_ids,
            brightness_multiplier=config.bridge.brightness_multiplier,
            red_gain=config.bridge.red_gain,
            green_gain=config.bridge.green_gain,
            blue_gain=config.bridge.blue_gain,
            color_correction_matrix=config.bridge.color_correction_matrix,
            saturation=config.bridge.saturation,
            white_mix=config.bridge.white_mix,
            white_mix_strategy=config.bridge.white_mix_strategy,
            white_extraction=config.bridge.white_extraction,
            white_gain=config.bridge.white_gain,
            rgbw_tint_gain=config.bridge.rgbw_tint_gain,
            white_cool_blue_boost=config.bridge.white_cool_blue_boost,
            black_floor_white=config.bridge.black_floor_white,
            black_floor_white_level=config.bridge.black_floor_white_level,
            black_floor_threshold=config.bridge.black_floor_threshold,
            max_brightness=config.bridge.max_brightness,
            use_white_channel=config.wled.use_white_channel,
        )
        self.smoother = TimeBasedSegmentSmoother(
            config.smoothing.enabled,
            config.smoothing.time_constant_ms / 1000.0,
        )
        self._last_sent: dict[int, FloatRGB] | None = None
        self._ambilight_active = False
        self._last_timing_log = 0.0
        self._segment_ids = {getattr(config.segment_ids, field.name) for field in fields(config.segment_ids)}
        self._target_lock = Lock()
        self._stats_lock = Lock()
        self._target_segments: dict[int, FloatRGB] | None = None
        self._display_segments: dict[int, FloatRGB] | None = None
        self._last_tv_success = time.monotonic()
        self._last_tv_warning = 0.0
        self._last_wled_warning = 0.0
        self._last_render_dt = 0.0
        self._last_render_alpha = 1.0
        self._stats = TimingStats()

    def enter_ambilight_mode(self) -> None:
        LOGGER.info(
            "Loading WLED Ambilight preset %s",
            self.config.wled.ambilight_preset_id,
        )
        self.wled.load_ambilight_preset()
        if self.config.bridge.preset_settle_seconds:
            time.sleep(self.config.bridge.preset_settle_seconds)
        self.wled.normalize_ambilight_state(self._segment_ids)
        self.output.start()
        self._ambilight_active = True
        self.smoother.reset()
        self._last_sent = None
        self._display_segments = None

    def restore_normal_mode(self) -> None:
        LOGGER.info("Restoring WLED normal preset %s", self.config.wled.normal_preset_id)
        self.output.stop()
        self.wled.restore_normal_preset()
        self._ambilight_active = False
        self.smoother.reset()
        self._last_sent = None
        self._display_segments = None

    def step(self) -> dict[int, FloatRGB]:
        started = time.perf_counter()
        target = self._poll_philips_once()
        after_tv = time.perf_counter()
        display, alpha = self.smoother.smooth(target, 0.0)
        after_smooth = time.perf_counter()
        compressed = self._compress(display)
        after_compression = time.perf_counter()
        self.output.send_segments(compressed)
        self._last_sent = dict(compressed)
        self._display_segments = dict(display)
        self._last_render_dt = 0.0
        self._last_render_alpha = alpha
        after_wled = time.perf_counter()
        self._log_step_timing(after_tv, after_smooth, after_compression, after_wled, started)
        return compressed

    def _compress(self, colors: dict[int, FloatRGB]) -> dict[int, FloatRGB]:
        compression = self.config.intensity_compression
        return {
            segment_id: compress_peak_intensity(
                color,
                enabled=compression.enabled,
                strength=compression.strength,
                curve=compression.curve,
            ).force_white(0.0)
            for segment_id, color in colors.items()
        }

    def run_forever(self, stop_event: Event | None = None) -> None:
        stop = stop_event or Event()
        philips_interval = self.config.timing.philips_poll_interval_ms / 1000.0
        wled_interval = self.config.timing.wled_render_interval_ms / 1000.0
        normal_restored_after_loss = False

        self.enter_ambilight_mode()
        poll_thread = Thread(
            target=self._poll_loop,
            args=(stop, philips_interval),
            name="ambilight-philips-poll",
            daemon=True,
        )
        poll_thread.start()
        LOGGER.info(
            "Bridge loop started at %.0f ms Philips poll / %.0f ms WLED render intervals",
            philips_interval * 1000,
            wled_interval * 1000,
        )

        next_render = time.monotonic()
        last_render_time: float | None = None

        try:
            while not stop.is_set():
                now = time.monotonic()
                target = self._get_target_segments()

                if target is not None and now >= next_render:
                    if not self._ambilight_active:
                        self.enter_ambilight_mode()
                        last_render_time = None
                    dt = 0.0 if last_render_time is None else max(0.0, now - last_render_time)
                    self._render_target(target, dt, now)
                    last_render_time = now
                    normal_restored_after_loss = False
                    next_render = now + wled_interval
                    self._log_loop_timing()

                last_tv_success = self._get_last_tv_success()
                if self._ambilight_active and self._should_restore_after_tv_loss(last_tv_success, normal_restored_after_loss):
                    normal_restored_after_loss = self._handle_tv_loss(
                        last_tv_success,
                        normal_restored_after_loss,
                    )
                    self._clear_target_segments()
                    last_render_time = None

                self._wait_until_next_event(stop, next_render)
        finally:
            stop.set()
            poll_thread.join(timeout=1.0)
            if self.config.bridge.restore_normal_on_exit:
                try:
                    self.restore_normal_mode()
                except WLEDAPIError as exc:
                    LOGGER.error("Could not restore normal WLED preset on exit: %s", exc)
            else:
                self.output.stop()

    def _handle_tv_loss(self, last_tv_success: float, already_restored: bool) -> bool:
        restore_after = self.config.bridge.restore_normal_after_tv_loss_seconds
        if restore_after is None or already_restored:
            return already_restored

        outage_seconds = time.monotonic() - last_tv_success
        if outage_seconds < restore_after:
            return False

        LOGGER.warning(
            "No TV Ambilight data for %.1f seconds; restoring normal preset once",
            outage_seconds,
        )
        try:
            self.restore_normal_mode()
            return True
        except WLEDAPIError as exc:
            LOGGER.warning("Could not restore normal preset after TV loss: %s", exc)
            return False

    @staticmethod
    def _wait(stop_event: Event, seconds: float) -> None:
        if seconds > 0:
            stop_event.wait(seconds)

    def _poll_loop(self, stop: Event, interval: float) -> None:
        next_poll = time.monotonic()
        while not stop.is_set():
            now = time.monotonic()
            if now < next_poll:
                self._wait(stop, min(next_poll - now, 0.050))
                continue

            started = time.monotonic()
            try:
                target = self._poll_philips_once()
                self._set_target_segments(target)
                self._record_tv_poll(started, time.monotonic() - started, success=True)
            except (PhilipsAPIError, MappingError) as exc:
                self._record_tv_poll(started, time.monotonic() - started, success=False)
                self._warn_rate_limited("tv", "TV Ambilight read failed: %s", exc)

            next_poll = _advance_deadline(next_poll, interval, time.monotonic())

    def _poll_philips_once(self) -> dict[int, FloatRGB]:
        payload = self.philips.processed()
        target = self.mapper.map_processed_float(payload)
        with self._target_lock:
            self._last_tv_success = time.monotonic()
        return target

    def _render_target(self, target: dict[int, FloatRGB], dt: float, started: float) -> None:
        display, alpha = self.smoother.smooth(target, dt)
        compressed = self._compress(display)
        wled_started = time.monotonic()
        try:
            self.output.send_segments(compressed)
            self._last_sent = dict(compressed)
            self._display_segments = dict(display)
            self._last_render_dt = dt
            self._last_render_alpha = alpha
            self._record_wled_render(started, time.monotonic() - wled_started, success=True)
        except WLEDAPIError as exc:
            self._record_wled_render(started, time.monotonic() - wled_started, success=False)
            self._warn_rate_limited("wled", "WLED update failed: %s", exc)

    def _set_target_segments(self, target: dict[int, FloatRGB]) -> None:
        with self._target_lock:
            self._target_segments = dict(target)

    def _get_target_segments(self) -> dict[int, FloatRGB] | None:
        with self._target_lock:
            if self._target_segments is None:
                return None
            return dict(self._target_segments)

    def _clear_target_segments(self) -> None:
        with self._target_lock:
            self._target_segments = None

    def _get_last_tv_success(self) -> float:
        with self._target_lock:
            return self._last_tv_success

    def _should_restore_after_tv_loss(self, last_tv_success: float, already_restored: bool) -> bool:
        restore_after = self.config.bridge.restore_normal_after_tv_loss_seconds
        if restore_after is None or already_restored:
            return False
        return time.monotonic() - last_tv_success >= restore_after

    def _wait_until_next_event(self, stop: Event, next_render: float) -> None:
        now = time.monotonic()
        wait_seconds = max(0.001, min(0.010, next_render - now))
        self._wait(stop, wait_seconds)

    def _warn_rate_limited(self, source: str, message: str, exc: Exception) -> None:
        now = time.monotonic()
        if source == "tv":
            if now - self._last_tv_warning < self.config.bridge.timing_log_interval_seconds:
                return
            self._last_tv_warning = now
        else:
            if now - self._last_wled_warning < self.config.bridge.timing_log_interval_seconds:
                return
            self._last_wled_warning = now
        LOGGER.warning(message, exc)

    def _record_tv_poll(self, started: float, latency: float, success: bool) -> None:
        with self._stats_lock:
            if self._stats.last_tv_poll_started is not None:
                self._stats.tv_poll_interval_total += started - self._stats.last_tv_poll_started
            self._stats.last_tv_poll_started = started
            if success:
                self._stats.tv_polls += 1
                self._stats.tv_request_latency_total += latency
            else:
                self._stats.tv_failures += 1

    def _record_wled_render(self, started: float, latency: float, success: bool) -> None:
        with self._stats_lock:
            if self._stats.last_wled_render_started is not None:
                self._stats.wled_render_interval_total += started - self._stats.last_wled_render_started
            self._stats.last_wled_render_started = started
            if success:
                self._stats.wled_renders += 1
                self._stats.wled_request_latency_total += latency
            else:
                self._stats.wled_failures += 1

    def _stats_snapshot(self) -> TimingStats:
        with self._stats_lock:
            return TimingStats(
                tv_polls=self._stats.tv_polls,
                tv_poll_interval_total=self._stats.tv_poll_interval_total,
                tv_request_latency_total=self._stats.tv_request_latency_total,
                tv_failures=self._stats.tv_failures,
                wled_renders=self._stats.wled_renders,
                wled_render_interval_total=self._stats.wled_render_interval_total,
                wled_request_latency_total=self._stats.wled_request_latency_total,
                wled_failures=self._stats.wled_failures,
                last_tv_poll_started=self._stats.last_tv_poll_started,
                last_wled_render_started=self._stats.last_wled_render_started,
            )

    def _log_step_timing(
        self,
        after_tv: float,
        after_smooth: float,
        after_compression: float,
        after_wled: float,
        started: float,
    ) -> None:
        interval = self.config.bridge.timing_log_interval_seconds
        if interval <= 0 or not LOGGER.isEnabledFor(logging.DEBUG):
            return

        now = time.monotonic()
        if now - self._last_timing_log < interval:
            return

        self._last_timing_log = now
        LOGGER.debug(
            "Step timing: tv/map=%.3fs smooth=%.3fs compress=%.3fs wled=%.3fs total=%.3fs",
            after_tv - started,
            after_smooth - after_tv,
            after_compression - after_smooth,
            after_wled - after_compression,
            after_wled - started,
        )

    def _log_loop_timing(self) -> None:
        interval = self.config.bridge.timing_log_interval_seconds
        if interval <= 0 or not LOGGER.isEnabledFor(logging.DEBUG):
            return

        now = time.monotonic()
        if now - self._last_timing_log < interval:
            return

        self._last_timing_log = now
        stats = self._stats_snapshot()
        target = self._get_target_segments() or {}
        display = self._display_segments or {}
        segment_id = min(self._segment_ids)
        target_color = target.get(segment_id)
        display_color = display.get(segment_id)
        output = self._last_sent.get(segment_id).as_list(include_white=True) if self._last_sent else None
        LOGGER.debug(
            (
                "Loop timing: tv_interval_avg=%.3fs render_interval_avg=%.3fs "
                "tv_latency_avg=%.3fs output_latency_avg=%.3fs tv_failures=%d output_failures=%d "
                "output_backend=%s output_packets=%d backend_failures=%d "
                "seg%d_target=%s seg%d_display=%s seg%d_output=%s dt=%.3fs alpha=%.3f"
            ),
            _safe_average(stats.tv_poll_interval_total, max(0, stats.tv_polls - 1)),
            _safe_average(stats.wled_render_interval_total, max(0, stats.wled_renders - 1)),
            _safe_average(stats.tv_request_latency_total, stats.tv_polls),
            _safe_average(stats.wled_request_latency_total, stats.wled_renders),
            stats.tv_failures,
            stats.wled_failures,
            self.output.backend_name,
            self.output.packets_sent,
            self.output.send_failures,
            segment_id,
            target_color.as_list(include_white=True) if target_color else None,
            segment_id,
            _format_float_color(display_color),
            segment_id,
            output,
            self._last_render_dt,
            self._last_render_alpha,
        )


def _advance_deadline(deadline: float, interval: float, now: float) -> float:
    deadline += interval
    while deadline <= now:
        deadline += interval
    return deadline


def _safe_average(total: float, count: int) -> float:
    if count <= 0:
        return 0.0
    return total / count


def _format_float_color(color: FloatRGB | None) -> list[float] | None:
    if color is None:
        return None
    return [round(color.r, 3), round(color.g, 3), round(color.b, 3), round(color.w, 3)]
