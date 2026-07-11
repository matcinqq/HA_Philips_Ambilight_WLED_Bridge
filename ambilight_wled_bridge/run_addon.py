from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from ambilight_wled_bridge.models import BridgeError
from ambilight_wled_bridge.pairing import PhilipsTVPairer

OPTIONS_PATH = Path("/data/options.json")
BRIDGE_CONFIG_PATH = Path("/data/bridge-config.yaml")
PAIR_STATE_PATH = Path("/data/pair-state.json")

PAIR_DEVICE_NAME = "Home Assistant"
PAIR_DEVICE_OS = "Home Assistant OS"
PAIR_APP_NAME = "Ambilight WLED Bridge"
PAIR_APP_ID = "ambilight.wled.bridge"


def main() -> None:
    options = load_options(OPTIONS_PATH)
    mode = str(options.get("mode", "bridge"))
    log_option_presence(options)
    if mode == "pair":
        run_pair_mode(options, PAIR_STATE_PATH)
        return

    config = build_bridge_config(options)
    log_bridge_config_presence(config)
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


def run_pair_mode(options: dict[str, Any], state_path: Path) -> None:
    tv_host = required(options, "tv_host")
    pin = optional_text(options.get("pair_pin"))

    try:
        if pin:
            complete_pairing(tv_host, pin, state_path)
        else:
            request_pairing(tv_host, state_path)
    except BridgeError as exc:
        raise SystemExit(str(exc)) from exc


def request_pairing(tv_host: str, state_path: Path) -> None:
    pairer = PhilipsTVPairer(tv_host)
    print(f"Requesting a pairing PIN from Philips TV at {tv_host}...", flush=True)
    pair_response = pairer.request_pairing(
        device_name=PAIR_DEVICE_NAME,
        device_os=PAIR_DEVICE_OS,
        app_name=PAIR_APP_NAME,
        app_id=PAIR_APP_ID,
    )
    if pair_response.get("error_id") == "CONCURRENT_PAIRING":
        raise SystemExit("The TV reports another pairing is in progress. Wait about 60 seconds and try again.")

    state_path.write_text(
        json.dumps(
            {
                "tv_host": tv_host,
                "device_id": pairer.device_id,
                "pair_response": pair_response,
            }
        ),
        encoding="utf-8",
    )
    timeout = pair_response.get("timeout")
    timeout_text = f" within {timeout} seconds" if timeout else ""
    print("The TV should now show a pairing PIN.", flush=True)
    print(
        f"Put that PIN into the add-on option pair_pin and start the add-on again{timeout_text}.",
        flush=True,
    )


def complete_pairing(tv_host: str, pin: str, state_path: Path) -> None:
    if not state_path.exists():
        raise SystemExit(
            "No pending pairing request was found. Clear pair_pin, start the add-on once to request a PIN, "
            "then enter the new PIN and start it again."
        )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise SystemExit("Pending pairing state is malformed. Clear pair_pin and start pairing again.")

    pair_response = state.get("pair_response")
    device_id = state.get("device_id")
    state_tv_host = state.get("tv_host")
    if state_tv_host != tv_host:
        raise SystemExit("Pending pairing state is for a different TV host. Clear pair_pin and start pairing again.")
    if not isinstance(pair_response, dict) or not isinstance(device_id, str) or not device_id:
        raise SystemExit("Pending pairing state is incomplete. Clear pair_pin and start pairing again.")

    pairer = PhilipsTVPairer(tv_host, device_id=device_id)
    credentials = pairer.grant_pairing(
        pin,
        pair_response,
        device_name=PAIR_DEVICE_NAME,
        device_os=PAIR_DEVICE_OS,
        app_name=PAIR_APP_NAME,
        app_id=PAIR_APP_ID,
    )
    pairer.test_credentials(credentials)
    try:
        state_path.unlink()
    except FileNotFoundError:
        pass

    print("Credential test against ambilight/topology succeeded.", flush=True)
    print("\nPairing successful.", flush=True)
    print("Copy these values into the add-on options:", flush=True)
    print(f"tv_username: {credentials.username}", flush=True)
    print(f"tv_password: {credentials.password}", flush=True)
    print("Then set mode to bridge and clear pair_pin.", flush=True)


def log_option_presence(options: dict[str, Any]) -> None:
    mode = str(options.get("mode", "bridge"))
    keys = ", ".join(sorted(str(key) for key in options))
    print(f"Add-on mode: {mode}", flush=True)
    print(
        "Add-on option presence: "
        f"tv_host={present(options.get('tv_host'))}, "
        f"tv_username={present(options.get('tv_username'))}, "
        f"tv_password={present(options.get('tv_password'))}, "
        f"pair_pin={present(options.get('pair_pin'))}, "
        f"wled_host={present(options.get('wled_host'))}, "
        f"ddp_host={present(options.get('ddp_host'))}",
        flush=True,
    )
    print(f"Add-on option keys: {keys}", flush=True)


def log_bridge_config_presence(config: dict[str, Any]) -> None:
    print(
        "Generated bridge config presence: "
        f"tv.host={present(config.get('tv', {}).get('host'))}, "
        f"wled.host={present(config.get('wled', {}).get('host'))}, "
        f"ddp.host={present(config.get('ddp', {}).get('host'))}, "
        f"output.backend={config.get('output', {}).get('backend')}",
        flush=True,
    )


def present(value: object) -> str:
    if value is None:
        return "missing"
    if str(value).strip() == "":
        return "blank"
    return "set"


def optional_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def required(options: dict[str, Any], key: str) -> str:
    value = options.get(key)
    if value is None or str(value).strip() == "":
        raise SystemExit(f"Required Home Assistant option is missing: {key}")
    return str(value)


if __name__ == "__main__":
    main()
