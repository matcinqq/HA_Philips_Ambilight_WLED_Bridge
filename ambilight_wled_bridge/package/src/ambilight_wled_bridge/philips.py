from __future__ import annotations

from typing import Any

import requests
from requests.auth import HTTPDigestAuth
from urllib3.exceptions import InsecureRequestWarning

from .config import TVConfig
from .models import PhilipsAPIError


class PhilipsClient:
    def __init__(self, config: TVConfig, session: requests.Session | None = None) -> None:
        if not config.username or not config.password:
            raise PhilipsAPIError("Philips TV credentials are required")
        self.config = config
        self.session = session or requests.Session()
        self.session.auth = HTTPDigestAuth(config.username, config.password)

        if not config.verify_tls:
            requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

    @property
    def base_url(self) -> str:
        return f"https://{self.config.host}:{self.config.port}/{self.config.api_version}"

    def system(self) -> dict[str, Any]:
        return self._get_json("/system")

    def topology(self) -> dict[str, Any]:
        return self._get_json("/ambilight/topology")

    def processed(self) -> dict[str, Any]:
        return self._get_json("/ambilight/processed")

    def measured(self) -> dict[str, Any]:
        return self._get_json("/ambilight/measured")

    def _get_json(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.get(
                url,
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_tls,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise PhilipsAPIError(f"Philips request failed for {path}: {exc}") from exc
        except ValueError as exc:
            raise PhilipsAPIError(f"Philips returned malformed JSON for {path}") from exc

        if not isinstance(data, dict):
            raise PhilipsAPIError(f"Philips response for {path} must be a JSON object")
        return data
