"""LAN discovery responder for the VigorSync Core API."""

from __future__ import annotations

import json
import socket
import threading
from typing import Any


DISCOVERY_PROBE = b"VIGORSYNC_DISCOVER/1"
DISCOVERY_SERVICE = "vigorsync-core"


def build_discovery_response(
    *,
    core_ip: str,
    core_port: int,
    device_name: str,
) -> dict[str, Any]:
    """Build a discovery reply without exposing pairing secrets."""

    return {
        "service": DISCOVERY_SERVICE,
        "version": 1,
        "core_url": f"http://{core_ip}:{core_port}",
        "device_name": device_name,
        "hostname": socket.gethostname(),
    }


class DiscoveryResponder:
    """Answer UDP discovery probes from paired mobile clients."""

    def __init__(
        self,
        *,
        port: int,
        core_port: int,
        device_name: str,
        host: str = "0.0.0.0",
    ) -> None:
        self.port = port
        self.core_port = core_port
        self.device_name = device_name
        self.host = host
        self._stop = threading.Event()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.settimeout(0.5)
        except OSError:
            sock.close()
            raise
        self._socket = sock
        self._thread = threading.Thread(
            target=self._serve,
            name="vigorsync-discovery",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _serve(self) -> None:
        sock = self._socket
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                payload, address = sock.recvfrom(1024)
            except TimeoutError:
                continue
            except OSError:
                break
            if payload.strip() != DISCOVERY_PROBE:
                continue
            response = build_discovery_response(
                core_ip=_local_ip_for(address[0]),
                core_port=self.core_port,
                device_name=self.device_name,
            )
            try:
                sock.sendto(
                    json.dumps(response, ensure_ascii=False).encode("utf-8"),
                    address,
                )
            except OSError:
                break


def _local_ip_for(peer_ip: str) -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((peer_ip, 9))
        return probe.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        probe.close()
