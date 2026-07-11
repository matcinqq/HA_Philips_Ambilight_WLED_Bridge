from __future__ import annotations

import base64
import hashlib
import hmac
import random
import string
from dataclasses import dataclass
from typing import Any

import requests
from requests.auth import HTTPDigestAuth
from urllib3.exceptions import InsecureRequestWarning

from .models import PhilipsAPIError

SECRET_KEY = "ZmVay1EQVFOaZhwQ4Kv81ypLAZNczV9sG4KkseXWn1NEk6cXmPKO/MCa9sryslvLCFMnNe4Z4CPXzToowvhHvA=="


@dataclass(frozen=True, slots=True)
class PairingCredentials:
    username: str
    password: str


class PhilipsTVPairer:
    def __init__(
        self,
        host: str,
        *,
        port: int = 1926,
        api_version: int = 6,
        timeout_seconds: float = 10.0,
        verify_tls: bool = False,
        session: requests.Session | None = None,
        device_id: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.api_version = api_version
        self.timeout_seconds = timeout_seconds
        self.verify_tls = verify_tls
        self.session = session or requests.Session()
        self.device_id = device_id or make_device_id()
        if not verify_tls:
            requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

    @property
    def base_url(self) -> str:
        return f"https://{self.host}:{self.port}/{self.api_version}"

    def request_pairing(
        self,
        *,
        device_name: str = "Home Assistant",
        device_os: str = "Home Assistant OS",
        app_name: str = "Ambilight WLED Bridge",
        app_id: str = "ambilight.wled.bridge",
    ) -> dict[str, Any]:
        device = build_device_payload(
            self.device_id,
            device_name=device_name,
            device_os=device_os,
            app_name=app_name,
            app_id=app_id,
        )
        return self._post_json(
            "/pair/request",
            {
                "scope": ["read", "write", "control"],
                "device": device,
            },
        )

    def grant_pairing(
        self,
        pin: str,
        pair_response: dict[str, Any],
        *,
        device_name: str = "Home Assistant",
        device_os: str = "Home Assistant OS",
        app_name: str = "Ambilight WLED Bridge",
        app_id: str = "ambilight.wled.bridge",
    ) -> PairingCredentials:
        auth_key = pair_response.get("auth_key")
        timestamp = pair_response.get("timestamp")
        if not isinstance(auth_key, str) or not auth_key:
            raise PhilipsAPIError("Pairing response did not include auth_key")
        if timestamp is None:
            raise PhilipsAPIError("Pairing response did not include timestamp")

        device = build_device_payload(
            self.device_id,
            device_name=device_name,
            device_os=device_os,
            app_name=app_name,
            app_id=app_id,
        )
        device["auth_key"] = auth_key
        payload = build_grant_payload(pin, timestamp, device)
        response = self._post_json(
            "/pair/grant",
            payload,
            auth=HTTPDigestAuth(self.device_id, auth_key),
        )
        if response.get("error_id") != "SUCCESS":
            error = response.get("error_id") or "unknown error"
            raise PhilipsAPIError(f"Philips pairing grant failed: {error}")
        return PairingCredentials(username=self.device_id, password=auth_key)

    def test_credentials(self, credentials: PairingCredentials) -> None:
        auth = HTTPDigestAuth(credentials.username, credentials.password)
        self._get_json("/ambilight/topology", auth=auth)

    def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        auth: HTTPDigestAuth | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.post(
                url,
                json=payload,
                auth=auth,
                verify=self.verify_tls,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json() if response.content else {}
        except requests.RequestException as exc:
            raise PhilipsAPIError(f"Philips pairing request failed for {path}: {exc}") from exc
        except ValueError as exc:
            raise PhilipsAPIError(f"Philips pairing response for {path} was malformed JSON") from exc
        if not isinstance(data, dict):
            raise PhilipsAPIError(f"Philips pairing response for {path} must be a JSON object")
        return data

    def _get_json(self, path: str, *, auth: HTTPDigestAuth | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.get(
                url,
                auth=auth,
                verify=self.verify_tls,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json() if response.content else {}
        except requests.RequestException as exc:
            raise PhilipsAPIError(f"Philips credential test failed for {path}: {exc}") from exc
        except ValueError as exc:
            raise PhilipsAPIError(f"Philips credential test response for {path} was malformed JSON") from exc
        if not isinstance(data, dict):
            raise PhilipsAPIError(f"Philips credential test response for {path} must be a JSON object")
        return data


def make_device_id(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.SystemRandom().choice(alphabet) for _ in range(length))


def create_signature(timestamp: object, pin: str) -> str:
    key = base64.b64decode(SECRET_KEY)
    message = (str(timestamp) + str(pin)).encode("utf-8")
    digest = hmac.new(key, message, hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_device_payload(
    device_id: str,
    *,
    device_name: str,
    device_os: str,
    app_name: str,
    app_id: str,
) -> dict[str, Any]:
    return {
        "device_name": device_name,
        "device_os": device_os,
        "app_name": app_name,
        "type": "native",
        "app_id": app_id,
        "id": device_id,
    }


def build_grant_payload(pin: str, timestamp: object, device: dict[str, Any]) -> dict[str, Any]:
    return {
        "auth": {
            "pin": pin,
            "auth_AppId": "1",
            "auth_timestamp": timestamp,
            "auth_signature": create_signature(timestamp, pin),
        },
        "device": dict(device),
    }
