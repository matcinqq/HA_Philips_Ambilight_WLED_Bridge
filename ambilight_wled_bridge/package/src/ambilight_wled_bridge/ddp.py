from __future__ import annotations

import logging
import socket
from collections.abc import Callable, Sequence

from .models import ColorValue, WLEDAPIError, clamp_channel

LOGGER = logging.getLogger(__name__)

DDP_DEFAULT_PORT = 4048
DDP_HEADER_SIZE = 10
DDP_FLAGS_VER1 = 0x40
DDP_FLAGS_PUSH = 0x01
DDP_TYPE_RGB24 = 0x0A
DDP_ID_DISPLAY = 0x01


class DDPClient:
    def __init__(
        self,
        host: str,
        port: int = DDP_DEFAULT_PORT,
        *,
        socket_factory: Callable[[int, int], socket.socket] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._socket_factory = socket_factory or socket.socket
        self._socket: socket.socket | None = None
        self._sequence = 0
        self.packets_sent = 0
        self.send_failures = 0
        self._logged_packet_length = False

    def start(self) -> None:
        if self._socket is not None:
            return
        try:
            self._socket = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        except OSError as exc:
            raise WLEDAPIError(f"Could not open DDP UDP socket: {exc}") from exc

    def stop(self) -> None:
        if self._socket is None:
            return
        try:
            self._socket.close()
        finally:
            self._socket = None

    def send_pixels(self, pixels: Sequence[ColorValue]) -> None:
        self.start()
        packet = self.build_packet(pixels)
        if not self._logged_packet_length and LOGGER.isEnabledFor(logging.DEBUG):
            LOGGER.debug("DDP packet length: %d bytes", len(packet))
            self._logged_packet_length = True

        if self._socket is None:  # pragma: no cover - start() guarantees this
            raise WLEDAPIError("DDP UDP socket is not open")
        try:
            sent = self._socket.sendto(packet, (self.host, self.port))
        except OSError as exc:
            self.send_failures += 1
            raise WLEDAPIError(f"DDP UDP send failed: {exc}") from exc
        if sent != len(packet):
            self.send_failures += 1
            raise WLEDAPIError(f"DDP UDP send wrote {sent} of {len(packet)} bytes")
        self.packets_sent += 1

    def build_packet(self, pixels: Sequence[ColorValue], offset: int = 0) -> bytes:
        if offset < 0:
            raise ValueError("offset must not be negative")
        payload = pixels_to_rgb_payload(pixels)
        if len(payload) > 0xFFFF:
            raise ValueError("DDP payload is too large for one packet")

        header = bytes(
            (
                DDP_FLAGS_VER1 | DDP_FLAGS_PUSH,
                self._sequence & 0x0F,
                DDP_TYPE_RGB24,
                DDP_ID_DISPLAY,
            )
        )
        header += int(offset).to_bytes(4, "big")
        header += len(payload).to_bytes(2, "big")
        self._sequence = (self._sequence + 1) % 16
        return header + payload


def pixels_to_rgb_payload(pixels: Sequence[ColorValue]) -> bytes:
    payload = bytearray()
    for pixel in pixels:
        payload.append(clamp_channel(pixel.r))
        payload.append(clamp_channel(pixel.g))
        payload.append(clamp_channel(pixel.b))
    return bytes(payload)
