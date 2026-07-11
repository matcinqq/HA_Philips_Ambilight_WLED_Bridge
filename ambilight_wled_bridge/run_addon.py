from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

OPTIONS_PATH = Path("/data/options.json")
BRIDGE_CONFIG_PATH = Path("/data/bridge-config.yaml")


def main() -> None:
    options = load_options(OPTIONS_PATH)
    if str(options.get("mode", "bridge")) == "pair":
        os.execvp("python3", build_pair_argv(options))

    config = build_bridge_config(options)
    BRIDGE_CONFIG_PATH.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    os.execvp("python3", build_cli_argv(options, BRIDGE_CONFIG_PATH))


def load_options(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Home Assistant options file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("Home Assistant options must be a JSON object")
    return data


def build_bridge_config(options: dict[str, Any]) -> dict[str, Any]:
    tv_host = required(options, "tv_host")
    tv_username = required(options, "tv_username")
    tv_password = required(options, "tv_password")
    wled_host = required(options, "wled_host")
    ddp_host = str(options.get("ddp_host") or wled_host)

    return {
        "tv": {
            "host": tv_host,
            "username": tv_username,
            "password": tv_password,
        },
        "wled": {
            "host": wled_host,
            "normal_preset_id": int(options.get("normal_preset_id", 1)),
            "ambilight_preset_id": int(options.get("ambilight_preset_id", 2)),
            "live_transition": 0,
            "use_white_channel": True,
        },
        "output": {
            "backend": str(options.get("output_backend", "ddp_pixels")),
        },
        "ddp": {
            "host": ddp_host,
            "port": int(options.get("ddp_port", 4048)),
            "pixel_count": int(options.get("ddp_pixel_count", 86)),
        },
        "bridge": {
            "max_brightness": int(options.get("max_brightness", 255)),
            "restore_normal_on_exit": bool(options.get("restore_normal_on_exit", True)),
            "restore_normal_after_tv_loss_seconds": float(options.get("restore_normal_after_tv_loss_seconds", 30)),
            "timing_log_interval_seconds": float(options.get("timing_log_interval_seconds", 5)),
        },
        "timing": {
            "philips_poll_interval_ms": int(options.get("philips_poll_interval_ms", 50)),
            "wled_render_interval_ms": int(options.get("wled_render_interval_ms", 33)),
        },
        "smoothing": {
            "enabled": bool(options.get("smoothing_enabled", True)),
            "time_constant_ms": float(options.get("smoothing_time_constant_ms", 120)),
        },
        "intensity_compression": {
            "enabled": False,
        },
        "mapping": {
            "left": {
                "lower": "0",
                "middle": "1",
                "upper": "2",
            },
            "right": {
                "lower": "2",
                "middle": "1",
                "upper": "0",
            },
            "bottom_blend": {
                "center_left_t": 0.33,
                "center_right_t": 0.67,
            },
        },
        "segment_ids": {
            "left_lower": 0,
            "left_middle": 1,
            "left_upper": 2,
            "right_lower": 3,
            "right_middle": 4,
            "right_upper": 5,
            "bottom_outer_left": 6,
            "bottom_center_left": 7,
            "bottom_center_right": 8,
            "bottom_outer_right": 9,
        },
    }


def build_cli_argv(options: dict[str, Any], config_path: Path) -> list[str]:
    verbosity = {
        "warning": [],
        "info": ["-v"],
        "debug": ["-vv"],
        "trace": ["-vvv"],
    }[str(options.get("log_level", "info"))]
    return [
        "python3",
        "-m",
        "ambilight_wled_bridge",
        *verbosity,
        "-c",
        str(config_path),
        "run",
    ]


def build_pair_argv(options: dict[str, Any]) -> list[str]:
    tv_host = required(options, "tv_host")
    argv = [
        "python3",
        "-m",
        "ambilight_wled_bridge",
        "-v",
        "pair",
        "--tv-ip",
        tv_host,
        "--device-name",
        "Home Assistant",
        "--device-os",
        "Home Assistant OS",
        "--app-name",
        "Ambilight WLED Bridge",
        "--test",
    ]
    return argv


def required(options: dict[str, Any], key: str) -> str:
    value = options.get(key)
    if value is None or str(value).strip() == "":
        raise SystemExit(f"Required Home Assistant option is missing: {key}")
    return str(value)


if __name__ == "__main__":
    main()
