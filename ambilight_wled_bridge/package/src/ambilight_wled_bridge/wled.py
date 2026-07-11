from __future__ import annotations

from typing import Any

import requests

from .config import WLEDConfig
from .models import ColorValue, WLEDAPIError


class WLEDClient:
    def __init__(self, config: WLEDConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    @property
    def base_url(self) -> str:
        return f"http://{self.config.host}"

    def info(self) -> dict[str, Any]:
        return self._get_json("/json/info")

    def state(self) -> dict[str, Any]:
        return self._get_json("/json/state")

    def cfg(self) -> dict[str, Any]:
        return self._get_json("/json/cfg")

    def load_preset(self, preset_id: int) -> None:
        self.post_state({"ps": preset_id})

    def load_ambilight_preset(self) -> None:
        self.load_preset(self.config.ambilight_preset_id)

    def restore_normal_preset(self) -> None:
        self.load_preset(self.config.normal_preset_id)

    def normalize_ambilight_state(self, segment_ids: set[int]) -> None:
        self.post_state(self.build_activation_payload(segment_ids))

    def update_segment_colors(self, colors: dict[int, ColorValue]) -> None:
        self.post_state(self.build_segment_color_payload(colors, state_fields=False))

    def build_activation_payload(self, segment_ids: set[int]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "on": True,
            "bri": 255,
            "tt": 0,
            "seg": [
                {
                    "id": segment_id,
                    "on": True,
                    "bri": 255,
                    "frz": False,
                    "fx": 0,
                }
                for segment_id in sorted(segment_ids)
            ],
        }
        if self.config.suppress_udp_sync:
            payload["udpn"] = {"nn": True}
        return payload

    def build_segment_color_payload(
        self,
        colors: dict[int, ColorValue],
        *,
        include_white: bool | None = None,
        transition: int | None = None,
        global_brightness: int | None = None,
        segment_brightness: int | None = None,
        freeze: bool | None = None,
        state_fields: bool = True,
    ) -> dict[str, Any]:
        use_white = self.config.use_white_channel if include_white is None else include_white
        payload: dict[str, Any] = {"seg": []}
        if state_fields:
            payload["on"] = True
            payload["tt"] = self.config.live_transition if transition is None else transition
        elif transition is not None:
            payload["tt"] = transition
        if global_brightness is not None:
            payload["bri"] = global_brightness

        for segment_id, color in sorted(colors.items()):
            segment: dict[str, Any] = {
                "id": segment_id,
                "col": [color.as_list(include_white=use_white)],
            }
            if state_fields:
                segment["on"] = True
                segment["fx"] = 0
            if segment_brightness is not None:
                segment["bri"] = segment_brightness
            if freeze is not None:
                segment["frz"] = freeze
            payload["seg"].append(segment)

        if self.config.suppress_udp_sync:
            payload["udpn"] = {"nn": True}
        return payload

    def post_state(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._post_json("/json/state", payload)

    def _get_json(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.get(url, timeout=self.config.timeout_seconds)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise WLEDAPIError(f"WLED request failed for {path}: {exc}") from exc
        except ValueError as exc:
            raise WLEDAPIError(f"WLED returned malformed JSON for {path}") from exc

        if not isinstance(data, dict):
            raise WLEDAPIError(f"WLED response for {path} must be a JSON object")
        return data

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.post(
                url,
                json=payload,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            if not response.content:
                return None
            data = response.json()
        except requests.RequestException as exc:
            raise WLEDAPIError(f"WLED request failed for {path}: {exc}") from exc
        except ValueError as exc:
            raise WLEDAPIError(f"WLED returned malformed JSON for {path}") from exc

        if data is not None and not isinstance(data, dict):
            raise WLEDAPIError(f"WLED response for {path} must be a JSON object")
        return data
