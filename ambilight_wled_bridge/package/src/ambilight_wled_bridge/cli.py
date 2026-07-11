from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from dataclasses import fields
from pathlib import Path
from threading import Event, Lock, Thread

from .bridge import AmbilightBridge
from .config import AppConfig, load_config, load_dotenv
from .diagnostics import colors_to_json, compare_sent_to_readback, extract_segment_colors
from .ddp import DDPClient
from .mapping import AmbilightMapper, logical_color_items
from .models import BridgeError, ConfigError, RGB
from .output import describe_pixel_ranges, expand_segments_to_pixels
from .pairing import PhilipsTVPairer
from .philips import PhilipsClient
from .smoothing import TimeBasedSegmentSmoother
from .transforms import PeakCompressionResult, compress_peak_intensity, trace_peak_intensity_compression
from .wled import WLEDClient


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    try:
        load_dotenv(args.env_file)
        config = _load_config_from_args(args)
        return args.func(args, config)
    except BridgeError as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Philips Ambilight to WLED bridge")
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="YAML config path; defaults to config.yaml when present",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="dotenv file for Philips credentials",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)

    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="Check TV and WLED connectivity")
    check.set_defaults(func=command_check)

    pair = subparsers.add_parser("pair", help="Pair this device with a Philips TV")
    pair.add_argument("--tv-ip", help="Philips TV IP/host; defaults to tv.host from config/env")
    pair.add_argument("--pin", help="PIN shown on the TV; omit for interactive prompt")
    pair.add_argument("--device-name", default="Home Assistant", help="Device name shown to the TV")
    pair.add_argument("--device-os", default="Home Assistant OS", help="Device OS shown to the TV")
    pair.add_argument("--app-name", default="Ambilight WLED Bridge", help="App name shown to the TV")
    pair.add_argument("--app-id", default="ambilight.wled.bridge", help="App ID shown to the TV")
    pair.add_argument("--timeout", type=float, default=10.0, help="Philips pairing request timeout in seconds")
    pair.add_argument("--verify-tls", action="store_true", help="Verify Philips TV TLS certificate")
    pair.add_argument("--test", action="store_true", help="Test generated credentials against ambilight/topology")
    pair.set_defaults(func=command_pair)

    once = subparsers.add_parser("once", help="Poll one TV frame and print mapped segment colors")
    once.add_argument("--send", action="store_true", help="Send the one mapped frame to WLED")
    once.add_argument("--raw", action="store_true", help="Include raw Philips processed payload")
    once.add_argument("--timing", action="store_true", help="Include TV/map/WLED timing")
    once.add_argument(
        "--source",
        choices=("processed", "measured"),
        default="processed",
        help="Philips Ambilight endpoint to sample; bridge run still uses processed",
    )
    once.set_defaults(func=command_once)

    debug_frame = subparsers.add_parser("debug-frame", help="Print one-frame forensic color pipeline")
    debug_frame.add_argument(
        "--source",
        choices=("processed", "measured"),
        default="processed",
        help="Philips Ambilight endpoint to sample",
    )
    debug_frame.set_defaults(func=command_debug_frame)

    debug_timing = subparsers.add_parser("debug-timing", help="Trace one segment through time-based smoothing")
    debug_timing.add_argument("--segment", type=int, default=0, help="Segment ID to trace")
    debug_timing.add_argument("--duration", type=float, default=5.0, help="Trace duration in seconds")
    debug_timing.add_argument(
        "--source",
        choices=("processed", "measured"),
        default="processed",
        help="Philips Ambilight endpoint to sample",
    )
    debug_timing.set_defaults(func=command_debug_timing)

    backend_info = subparsers.add_parser("backend-info", help="Print configured live output backend")
    backend_info.set_defaults(func=command_backend_info)

    debug_ddp_pixels = subparsers.add_parser("debug-ddp-pixels", help="Print DDP pixel expansion for one TV frame")
    debug_ddp_pixels.add_argument(
        "--source",
        choices=("processed", "measured"),
        default="processed",
        help="Philips Ambilight endpoint to sample",
    )
    debug_ddp_pixels.set_defaults(func=command_debug_ddp_pixels)

    raw_passthrough = subparsers.add_parser(
        "raw-passthrough",
        help="Send one raw RGB frame as RGBW [r,g,b,0] and read WLED back",
    )
    raw_passthrough.add_argument(
        "--source",
        choices=("processed", "measured"),
        default="processed",
        help="Philips Ambilight endpoint to sample",
    )
    raw_passthrough.add_argument("--dry-run", action="store_true", help="Print payload without sending")
    raw_passthrough.add_argument(
        "--skip-preset",
        action="store_true",
        help="Do not load the Ambilight preset before the diagnostic send",
    )
    raw_passthrough.set_defaults(func=command_raw_passthrough)

    run = subparsers.add_parser("run", help="Run the continuous bridge")
    run.set_defaults(func=command_run)

    test_ddp = subparsers.add_parser("test-ddp", help="Send static DDP test patterns")
    test_ddp.add_argument(
        "--pattern",
        choices=("all", "red", "green", "blue", "coral", "segments"),
        default="all",
        help="Pattern to send; defaults to cycling all patterns",
    )
    test_ddp.add_argument("--duration", type=float, default=1.0, help="Seconds to hold each pattern")
    test_ddp.add_argument("--skip-setup", action="store_true", help="Do not load/normalize the Ambilight preset first")
    test_ddp.add_argument("--no-restore", action="store_true", help="Do not restore the normal preset after the test")
    test_ddp.set_defaults(func=command_test_ddp)

    restore = subparsers.add_parser("restore-normal", help="Load the normal WLED preset and exit")
    restore.set_defaults(func=command_restore_normal)

    return parser


def configure_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("urllib3").setLevel(logging.DEBUG if verbosity >= 3 else logging.WARNING)


def command_check(_args: argparse.Namespace, config: AppConfig) -> int:
    config.validate(require_tv_credentials=True)
    philips = PhilipsClient(config.tv)
    wled = WLEDClient(config.wled)

    system = philips.system()
    topology = philips.topology()
    info = wled.info()
    cfg = wled.cfg()
    gamma = _extract_wled_color_gamma(cfg)

    result = {
        "tv": {
            "host": config.tv.host,
            "api_version": system.get("api_version"),
            "topology": topology,
        },
        "wled": {
            "host": config.wled.host,
            "brand": info.get("brand"),
            "product": info.get("product"),
            "leds": info.get("leds"),
            "color_gamma": gamma,
        },
        "presets": {
            "normal": config.wled.normal_preset_id,
            "ambilight": config.wled.ambilight_preset_id,
        },
        "output": _backend_info(config),
    }
    if gamma is not None and abs(gamma - 1.0) > 0.2:
        result["wled"]["gamma_warning"] = (
            f"WLED color gamma is {gamma}. This bridge is calibrated for approximately 1.0. "
            "Higher gamma may crush low values and overemphasize dominant channels."
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def command_pair(args: argparse.Namespace, config: AppConfig) -> int:
    tv_host = args.tv_ip or config.tv.host
    if not tv_host:
        raise ConfigError("Pairing needs --tv-ip or tv.host/PHILIPS_TV_HOST")
    if args.timeout <= 0:
        raise ConfigError("--timeout must be positive")

    pairer = PhilipsTVPairer(
        tv_host,
        port=config.tv.port,
        api_version=config.tv.api_version,
        timeout_seconds=args.timeout,
        verify_tls=args.verify_tls,
    )

    print(f"Requesting a pairing PIN from Philips TV at {tv_host}...")
    pair_response = pairer.request_pairing(
        device_name=args.device_name,
        device_os=args.device_os,
        app_name=args.app_name,
        app_id=args.app_id,
    )
    if pair_response.get("error_id") == "CONCURRENT_PAIRING":
        raise ConfigError("The TV reports another pairing is in progress. Wait about 60 seconds and try again.")

    timeout = pair_response.get("timeout")
    if timeout is not None:
        print(f"Enter the PIN shown on the TV. The TV reported a {timeout} second pairing window.")
    else:
        print("Enter the PIN shown on the TV.")

    pin = args.pin or input("PIN: ").strip()
    if not pin:
        raise ConfigError("PIN is required")

    credentials = pairer.grant_pairing(
        pin,
        pair_response,
        device_name=args.device_name,
        device_os=args.device_os,
        app_name=args.app_name,
        app_id=args.app_id,
    )
    if args.test:
        pairer.test_credentials(credentials)
        print("Credential test against ambilight/topology succeeded.")

    print("\nPairing successful.")
    print("Copy these values into the Home Assistant add-on options:")
    print(f"tv_username: {credentials.username}")
    print(f"tv_password: {credentials.password}")
    print("\nFor laptop CLI use, put them in .env as:")
    print(f"PHILIPS_TV_USER={credentials.username}")
    print(f"PHILIPS_TV_PASS={credentials.password}")
    return 0


def command_backend_info(_args: argparse.Namespace, config: AppConfig) -> int:
    config.validate(require_tv_credentials=False)
    print(json.dumps(_backend_info(config), indent=2, sort_keys=True))
    return 0


def command_once(args: argparse.Namespace, config: AppConfig) -> int:
    config.validate(require_tv_credentials=True)
    philips = PhilipsClient(config.tv)
    mapper = _mapper_from_config(config)
    started = time.perf_counter()
    payload = philips.measured() if args.source == "measured" else philips.processed()
    after_tv = time.perf_counter()
    mapped = mapper.map_processed_float(payload)
    colors = _compress_colors(mapped, config)
    after_map = time.perf_counter()

    if args.send:
        wled = WLEDClient(config.wled)
        wled.load_ambilight_preset()
        wled.update_segment_colors(colors)
    after_wled = time.perf_counter()

    colors_json = colors_to_json(colors, include_white=config.wled.use_white_channel)
    if args.raw or args.timing:
        output = {"source": args.source, "mapped": colors_json}
        if args.raw:
            output["raw"] = payload
        if args.timing:
            output["timing_seconds"] = {
                "tv": round(after_tv - started, 4),
                "map": round(after_map - after_tv, 4),
                "wled": round(after_wled - after_map, 4) if args.send else 0.0,
                "total": round(after_wled - started, 4),
            }
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print(json.dumps(colors_json, indent=2, sort_keys=True))
    return 0


def command_debug_frame(args: argparse.Namespace, config: AppConfig) -> int:
    config.validate(require_tv_credentials=True)
    philips = PhilipsClient(config.tv)
    mapper = _mapper_from_config(config)
    payload = philips.measured() if args.source == "measured" else philips.processed()

    raw_logical = mapper.map_logical_raw(payload)
    raw_segments = mapper.logical_to_segments(raw_logical)
    processed_segments = mapper.map_processed_float(payload)
    smoother = TimeBasedSegmentSmoother(
        config.smoothing.enabled,
        config.smoothing.time_constant_ms / 1000.0,
    )
    smoothed, _alpha = smoother.smooth(processed_segments, 0.0)
    compression_traces = _trace_compression(smoothed, config)
    compressed = {segment_id: trace.output.force_white(0.0) for segment_id, trace in compression_traces.items()}
    wled = WLEDClient(config.wled)
    wled_payload = wled.build_segment_color_payload(compressed, state_fields=False)

    _print_debug_frame(
        payload,
        mapper,
        raw_logical,
        raw_segments,
        smoothed,
        compression_traces,
        compressed,
        wled_payload,
        include_white=config.wled.use_white_channel,
    )
    return 0


def command_debug_timing(args: argparse.Namespace, config: AppConfig) -> int:
    config.validate(require_tv_credentials=True)
    philips = PhilipsClient(config.tv)
    mapper = _mapper_from_config(config)
    smoother = TimeBasedSegmentSmoother(
        config.smoothing.enabled,
        config.smoothing.time_constant_ms / 1000.0,
    )
    poll_interval = config.timing.philips_poll_interval_ms / 1000.0
    render_interval = config.timing.wled_render_interval_ms / 1000.0
    target_lock = Lock()
    stop = Event()
    target: dict[int, object] | None = None
    last_error: Exception | None = None

    def poll_loop() -> None:
        nonlocal target, last_error
        next_poll = time.monotonic()
        while not stop.is_set():
            now = time.monotonic()
            if now < next_poll:
                stop.wait(max(0.001, min(0.010, next_poll - now)))
                continue

            try:
                payload = philips.measured() if args.source == "measured" else philips.processed()
                mapped = mapper.map_processed_float(payload)
                with target_lock:
                    target = mapped
                    last_error = None
            except Exception as exc:  # pragma: no cover - exercised by live diagnostics
                with target_lock:
                    last_error = exc

            next_poll = _advance_deadline(next_poll, poll_interval, time.monotonic())

    started = time.monotonic()
    end_at = started + args.duration
    next_render = started
    last_render: float | None = None
    samples = 0

    print("time_s\ttarget_rgb\tdisplay_rgb_float\tfinal_rgbw\tdt_s\talpha")
    poll_thread = Thread(target=poll_loop, name="ambilight-debug-timing-poll", daemon=True)
    poll_thread.start()
    try:
        while time.monotonic() < end_at:
            now = time.monotonic()
            if now >= next_render:
                with target_lock:
                    current_target = dict(target) if target is not None else None
                    current_error = last_error

                if current_target is not None:
                    dt = 0.0 if last_render is None else max(0.0, now - last_render)
                    display, alpha = smoother.smooth(current_target, dt)
                    compressed = _compress_colors(display, config)
                    target_color = current_target.get(args.segment)
                    display_color = display.get(args.segment)
                    final_color = compressed.get(args.segment)
                    print(
                        "\t".join(
                            (
                                f"{time.monotonic() - started:.3f}",
                                _format_color(target_color),
                                _format_float_color(display_color),
                                _format_color(final_color, include_white=True),
                                f"{dt:.3f}",
                                f"{alpha:.6f}",
                            )
                        )
                    )
                    samples += 1
                    last_render = now
                    next_render = _advance_deadline(next_render, render_interval, time.monotonic())
                elif current_error is not None and time.monotonic() >= end_at:
                    raise current_error
                else:
                    next_render = now

            wait_until = min(next_render, end_at)
            time.sleep(max(0.001, min(0.010, wait_until - time.monotonic())))
    finally:
        stop.set()
        poll_thread.join(timeout=1.0)

    if samples == 0:
        with target_lock:
            current_error = last_error
        if current_error is not None:
            raise current_error
        raise ConfigError(
            "debug-timing did not receive a Philips Ambilight frame; "
            "try a longer --duration or check TV connectivity"
        )
    return 0


def command_debug_ddp_pixels(args: argparse.Namespace, config: AppConfig) -> int:
    config.validate(require_tv_credentials=True)
    philips = PhilipsClient(config.tv)
    mapper = _mapper_from_config(config)
    payload = philips.measured() if args.source == "measured" else philips.processed()
    segments = _compress_colors(mapper.map_processed_float(payload), config)
    pixels = expand_segments_to_pixels(segments, config.ddp.pixel_count)

    print("=== OUTPUT BACKEND ===")
    print(json.dumps(_backend_info(config), indent=2, sort_keys=True))
    print("\n=== SEGMENT COLORS ===")
    for segment_id in sorted(segments):
        print(f"seg{segment_id} color = {_format_color(segments[segment_id])}")

    print("\n=== DDP PIXEL EXPANSION ===")
    for segment_id, start, end in describe_pixel_ranges():
        color = segments[segment_id]
        print(f"pixels {start}..{end} = seg{segment_id} {_format_color(color)}")
    print(f"\npixel_count: {len(pixels)}")
    return 0


def command_raw_passthrough(args: argparse.Namespace, config: AppConfig) -> int:
    config.validate(require_tv_credentials=True)
    philips = PhilipsClient(config.tv)
    mapper = AmbilightMapper(mapping=config.mapping, segment_ids=config.segment_ids)
    payload = philips.measured() if args.source == "measured" else philips.processed()
    colors = mapper.map_raw_segments(payload)
    wled = WLEDClient(config.wled)
    wled_payload = wled.build_segment_color_payload(
        colors,
        include_white=True,
        transition=0,
        global_brightness=255,
        segment_brightness=255,
        freeze=False,
    )

    print("=== RAW PASSTHROUGH COLORS ===")
    print(json.dumps(colors_to_json(colors, include_white=True), indent=2, sort_keys=True))
    print("\n=== EXACT WLED JSON PAYLOAD ===")
    print(json.dumps(wled_payload, indent=2, sort_keys=True))

    if args.dry_run:
        print("\n=== SENT VS READ-BACK ===")
        print("dry-run: not sent")
        return 0

    if not args.skip_preset:
        wled.load_ambilight_preset()
    wled.post_state(wled_payload)
    state = wled.state()
    readback = extract_segment_colors(state, set(colors))
    comparison = compare_sent_to_readback(colors, readback, include_white=True)

    print("\n=== SENT VS READ-BACK ===")
    print(json.dumps(comparison, indent=2, sort_keys=True))
    print(f"\nall_match: {all(item['match'] for item in comparison.values())}")
    return 0


def command_test_ddp(args: argparse.Namespace, config: AppConfig) -> int:
    config.validate(require_tv_credentials=False)
    if args.duration <= 0:
        raise ConfigError("--duration must be positive")

    host = _ddp_host(config)
    client = DDPClient(host, config.ddp.port)
    wled = WLEDClient(config.wled)
    render_interval = config.timing.wled_render_interval_ms / 1000.0
    pattern_items = _ddp_test_patterns(config.ddp.pixel_count)
    if args.pattern != "all":
        pattern_items = [(name, pixels) for name, pixels in pattern_items if name == args.pattern]

    try:
        if not args.skip_setup:
            wled.load_ambilight_preset()
            if config.bridge.preset_settle_seconds:
                time.sleep(config.bridge.preset_settle_seconds)
            wled.normalize_ambilight_state(_segment_ids_from_config(config))

        client.start()
        for name, pixels in pattern_items:
            print(f"sending {name} via DDP to {host}:{config.ddp.port}")
            end_at = time.monotonic() + args.duration
            while time.monotonic() < end_at:
                client.send_pixels(pixels)
                time.sleep(render_interval)
    finally:
        client.stop()
        if not args.no_restore:
            wled.restore_normal_preset()

    print(f"ddp_packets_sent: {client.packets_sent}")
    return 0


def command_run(_args: argparse.Namespace, config: AppConfig) -> int:
    config.validate(require_tv_credentials=True)
    stop_event = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    AmbilightBridge(config).run_forever(stop_event)
    return 0


def command_restore_normal(_args: argparse.Namespace, config: AppConfig) -> int:
    config.validate(require_tv_credentials=False)
    WLEDClient(config.wled).restore_normal_preset()
    return 0


def _load_config_from_args(args: argparse.Namespace) -> AppConfig:
    config_path = Path(args.config)
    if args.config == "config.yaml" and not config_path.exists():
        config = load_config(None)
    else:
        config = load_config(config_path)

    if args.command in {"check", "once", "debug-frame", "debug-timing", "debug-ddp-pixels", "raw-passthrough", "run"}:
        config.validate(require_tv_credentials=True)
    elif args.command == "pair":
        config.validate(require_tv_credentials=False, require_wled=False)
    elif args.command in {"backend-info", "test-ddp", "restore-normal"}:
        config.validate(require_tv_credentials=False)
    else:
        raise ConfigError(f"Unknown command: {args.command}")
    return config


def _mapper_from_config(config: AppConfig) -> AmbilightMapper:
    return AmbilightMapper(
        config.mapping,
        config.segment_ids,
        config.bridge.brightness_multiplier,
        config.bridge.red_gain,
        config.bridge.green_gain,
        config.bridge.blue_gain,
        config.bridge.color_correction_matrix,
        config.bridge.saturation,
        config.bridge.white_mix,
        config.bridge.white_mix_strategy,
        config.bridge.white_extraction,
        config.bridge.white_gain,
        config.bridge.rgbw_tint_gain,
        config.bridge.white_cool_blue_boost,
        config.bridge.black_floor_white,
        config.bridge.black_floor_white_level,
        config.bridge.black_floor_threshold,
        config.bridge.max_brightness,
        config.wled.use_white_channel,
    )


def _compress_colors(colors: dict[int, object], config: AppConfig) -> dict[int, object]:
    compression = config.intensity_compression
    return {
        segment_id: compress_peak_intensity(
            color,
            enabled=compression.enabled,
            strength=compression.strength,
            curve=compression.curve,
        ).force_white(0.0)
        for segment_id, color in colors.items()
    }


def _trace_compression(colors: dict[int, object], config: AppConfig) -> dict[int, PeakCompressionResult]:
    compression = config.intensity_compression
    return {
        segment_id: trace_peak_intensity_compression(
            color,
            enabled=compression.enabled,
            strength=compression.strength,
            curve=compression.curve,
        )
        for segment_id, color in colors.items()
    }


def _print_debug_frame(
    philips_payload: object,
    mapper: AmbilightMapper,
    raw_logical: object,
    raw_segments: dict[int, RGB],
    smoothed: dict[int, object],
    compression_traces: dict[int, PeakCompressionResult],
    compressed: dict[int, object],
    wled_payload: dict[str, object],
    *,
    include_white: bool,
) -> None:
    print("=== PHILIPS RAW ===")
    _print_raw_philips_zones(philips_payload)

    print("\n=== ZONE MAPPING ===")
    source_labels = _logical_source_labels(mapper)
    for name, color in logical_color_items(raw_logical):
        if name.startswith("bottom_"):
            continue
        print(f"{name:19s} source {source_labels[name]:6s} = {_format_color(color)}")

    print("\n=== SYNTHETIC BOTTOM ===")
    for name, color in logical_color_items(raw_logical):
        if name.startswith("bottom_"):
            print(f"{name:19s} = {_format_color(color)}")

    traces = {segment_id: mapper.trace_color(color) for segment_id, color in raw_segments.items()}
    _print_trace_stage("AFTER GAIN", traces, "after_matrix_gain_brightness", include_white)
    _print_trace_stage("AFTER SATURATION", traces, "after_saturation", include_white)
    _print_trace_stage("AFTER WHITE MIX", traces, "after_white_mix", include_white)

    print("\n=== AFTER GAMMA / CURVE ===")
    print("disabled")

    _print_trace_stage("RGBW CONVERSION INPUT", traces, "rgbw_conversion_input", include_white)
    _print_trace_stage("RGBW CONVERSION OUTPUT", traces, "rgbw_conversion_output", include_white)
    _print_trace_stage("AFTER BLACK FLOOR", traces, "after_black_floor", include_white)
    _print_trace_stage("AFTER MAX BRIGHTNESS", traces, "after_max_brightness", include_white)

    print("\n=== SMOOTHING PREVIOUS STATE ===")
    print("empty: debug-frame creates a fresh smoother, so the first frame passes through")
    print("smoothing storage: float RGB state; W is forced to 0 in the baseline path")

    print("\n=== AFTER SMOOTHING ===")
    print(json.dumps(colors_to_json(smoothed, include_white=include_white), indent=2, sort_keys=True))

    print("\n=== INTENSITY COMPRESSION ===")
    for segment_id, trace in sorted(compression_traces.items()):
        state = "enabled" if trace.enabled else "disabled"
        print(
            f"seg{segment_id} {state}; input {_format_color(trace.input, include_white)}; "
            f"peak {trace.peak:.3f}; compressed_peak {trace.compressed_peak:.3f}; "
            f"scale {trace.scale:.6f}; output {_format_color(trace.output.force_white(0.0), include_white)}"
        )

    print("\n=== FINAL RGBW ===")
    print(json.dumps(colors_to_json(compressed, include_white=include_white), indent=2, sort_keys=True))

    print("\n=== AFTER ROUNDING ===")
    print("rounding/clamping happens at final RGBW serialization")

    print("\n=== EXACT WLED JSON PAYLOAD ===")
    print(json.dumps(wled_payload, indent=2, sort_keys=True))


def _print_raw_philips_zones(payload: object) -> None:
    if not isinstance(payload, dict):
        print("payload is not an object")
        return
    layer = payload.get("layer1")
    if not isinstance(layer, dict):
        print("payload.layer1 is not an object")
        return
    for side in ("left", "right"):
        zones = layer.get(side)
        if not isinstance(zones, dict):
            print(f"{side}: missing")
            continue
        for zone_id in sorted(zones, key=_zone_sort_key):
            color = RGB.from_mapping(zones[zone_id], f"payload.layer1.{side}.{zone_id}")
            print(f"{side}{zone_id} = {_format_color(color)}")


def _print_trace_stage(
    title: str,
    traces: dict[int, list[object]],
    stage_name: str,
    include_white: bool,
) -> None:
    print(f"\n=== {title} ===")
    for segment_id, stages in sorted(traces.items()):
        stage = next(stage for stage in stages if stage.name == stage_name)
        if stage.detail == "disabled":
            print(f"seg{segment_id} disabled; value {_format_color(stage.color, include_white)}")
        else:
            detail = f" ({stage.detail})" if stage.detail else ""
            print(f"seg{segment_id} {_format_color(stage.color, include_white)}{detail}")


def _logical_source_labels(mapper: AmbilightMapper) -> dict[str, str]:
    return {
        "left_lower": f"left{mapper.mapping.left.lower}",
        "left_middle": f"left{mapper.mapping.left.middle}",
        "left_upper": f"left{mapper.mapping.left.upper}",
        "right_lower": f"right{mapper.mapping.right.lower}",
        "right_middle": f"right{mapper.mapping.right.middle}",
        "right_upper": f"right{mapper.mapping.right.upper}",
    }


def _format_color(color: object | None, include_white: bool = False) -> str:
    if color is None:
        return "disabled"
    if not hasattr(color, "as_list"):
        return json.dumps(str(color))
    return json.dumps(color.as_list(include_white=include_white))


def _format_float_color(color: object | None) -> str:
    if color is None:
        return "null"
    return json.dumps([round(float(color.r), 3), round(float(color.g), 3), round(float(color.b), 3)])


def _backend_info(config: AppConfig) -> dict[str, object]:
    return {
        "output_backend": config.output.backend,
        "ddp_host": _ddp_host(config),
        "ddp_port": config.ddp.port,
        "ddp_pixel_count": config.ddp.pixel_count,
        "json_segments_endpoint": f"http://{config.wled.host}/json/state" if config.wled.host else None,
    }


def _ddp_host(config: AppConfig) -> str:
    host = config.ddp.host or config.wled.host
    if not host:
        raise ConfigError("ddp.host or wled.host is required")
    return host


def _segment_ids_from_config(config: AppConfig) -> set[int]:
    return {getattr(config.segment_ids, field.name) for field in fields(config.segment_ids)}


def _ddp_test_patterns(pixel_count: int) -> list[tuple[str, list[RGB]]]:
    segment_colors = {
        segment_id: RGB(
            (segment_id * 47 + 40) % 256,
            (segment_id * 83 + 20) % 256,
            (segment_id * 29 + 90) % 256,
        )
        for segment_id in range(10)
    }
    return [
        ("red", [RGB(255, 0, 0) for _ in range(pixel_count)]),
        ("green", [RGB(0, 255, 0) for _ in range(pixel_count)]),
        ("blue", [RGB(0, 0, 255) for _ in range(pixel_count)]),
        ("coral", [RGB(254, 86, 64) for _ in range(pixel_count)]),
        ("segments", [pixel.as_rgb() for pixel in expand_segments_to_pixels(segment_colors, pixel_count)]),
    ]


def _advance_deadline(deadline: float, interval: float, now: float) -> float:
    deadline += interval
    while deadline <= now:
        deadline += interval
    return deadline


def _extract_wled_color_gamma(cfg: object) -> float | None:
    if not isinstance(cfg, dict):
        return None
    light = cfg.get("light")
    if not isinstance(light, dict):
        return None
    gamma = light.get("gc")
    if not isinstance(gamma, dict):
        return None
    value = gamma.get("col")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _zone_sort_key(value: object) -> tuple[int, str]:
    text = str(value)
    try:
        return (0, f"{int(text):08d}")
    except ValueError:
        return (1, text)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
