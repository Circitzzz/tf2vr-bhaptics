"""Minimal bHaptics Player client (ws://127.0.0.1:15881/v2/feedbacks).

Stdlib only. Same wire format Suit Command Center already uses: masked text
frames carrying {"Submit": [{"type": "frame", "key", "frame": {...}}]}.
Each effect gets its own key so overlapping effects mix instead of replacing
each other, and nothing collides with Suit Command Center's own keys.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import time

HOST, PORT = "127.0.0.1", 15881
# app_id / app_name are how bHaptics Player labels a connected app.
APP_ID, APP_NAME = "com.tf2vr.haptics", "Titanfall 2"
PATH = "/v2/feedbacks?app_id=" + APP_ID + "&app_name=" + APP_NAME.replace(" ", "%20")
KEY_PREFIX = "tfh-"
HELLO_EVERY = 2.0


def _mask(body: bytes) -> bytes:
    header = b"\x81"
    n = len(body)
    if n < 126:
        header += bytes([0x80 | n])
    elif n < 1 << 16:
        header += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        header += bytes([0x80 | 127]) + struct.pack(">Q", n)
    key = os.urandom(4)
    return header + key + bytes(b ^ key[i % 4] for i, b in enumerate(body))


class BHaptics:
    """Keeps one connection to the Player, reconnecting as needed."""

    def __init__(self):
        self._sock = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.positions: list[str] = []
        self.motors: dict[str, list[int]] = {}
        self.connected = False
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="bhaptics")

    def start(self):
        self._thread.start()
        return self

    def wait_connected(self, timeout=5.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.connected and self.positions:
                return True
            time.sleep(0.05)
        return self.connected

    def close(self):
        self._stop.set()
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass

    # ---------------------------------------------------------- sending
    def _send(self, payload: dict) -> bool:
        body = json.dumps(payload, separators=(",", ":")).encode()
        with self._lock:
            sock = self._sock
            if sock is None:
                return False
            try:
                sock.sendall(_mask(body))
                return True
            except OSError:
                return False

    def dots(self, position: str, dots: dict[int, int], millis: int,
             key: str) -> bool:
        """dots = {motor_index: intensity 0-100}."""
        pts = [{"index": int(i), "intensity": max(0, min(100, int(v)))}
               for i, v in dots.items() if v > 0]
        if not pts:
            return False
        return self._send({"Submit": [{
            "type": "frame", "key": KEY_PREFIX + key,
            "frame": {"position": position,
                      "durationMillis": max(10, int(millis)),
                      "dotPoints": pts}}]})

    def stop_all(self):
        """Turn off everything this bridge is playing (Player 'turnOffAll')."""
        return self._send({"Submit": [{"type": "turnOffAll", "key": ""}]})

    # ---------------------------------------------------------- reading
    def _run(self):
        while not self._stop.is_set():
            try:
                self._session()
            except Exception:
                pass
            self.connected = False
            with self._lock:
                self._sock = None
            self._stop.wait(2.0)

    def _session(self):
        key = base64.b64encode(os.urandom(16)).decode()
        sock = socket.create_connection((HOST, PORT), timeout=4)
        sock.sendall((f"GET {PATH} HTTP/1.1\r\nHost: {HOST}:{PORT}\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                raise OSError("closed during handshake")
            buf += chunk
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise OSError("upgrade refused")
        pending = buf.split(b"\r\n\r\n", 1)[1]
        sock.settimeout(0.5)
        with self._lock:
            self._sock = sock
        self.connected = True
        last_hello = 0.0
        while not self._stop.is_set():
            if time.time() - last_hello > HELLO_EVERY:
                # Zero-intensity heartbeat keeps the Player pushing Status.
                self._send({"Submit": [{"type": "frame",
                            "key": KEY_PREFIX + "hello",
                            "frame": {"position": "VestFront",
                                      "durationMillis": 10,
                                      "dotPoints": [{"index": 0,
                                                     "intensity": 0}]}}]})
                last_hello = time.time()
            try:
                data = sock.recv(65536)
            except socket.timeout:
                continue
            if not data:
                return
            pending = self._consume(pending + data)

    def _consume(self, p: bytes) -> bytes:
        while len(p) >= 2:
            op, n, i = p[0] & 0x0F, p[1] & 0x7F, 2
            if n == 126:
                if len(p) < 4:
                    break
                n, i = struct.unpack(">H", p[2:4])[0], 4
            elif n == 127:
                if len(p) < 10:
                    break
                n, i = struct.unpack(">Q", p[2:10])[0], 10
            if len(p) < i + n:
                break
            body, p = p[i:i + n], p[i + n:]
            if op == 0x8:
                raise OSError("closed")
            if op == 0x1:
                try:
                    msg = json.loads(body)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    if "ConnectedPositions" in msg:
                        self.positions = [str(x) for x in
                                          msg.get("ConnectedPositions") or []]
                    st = msg.get("Status")
                    if isinstance(st, dict):
                        self.motors = {k: v for k, v in st.items()
                                       if isinstance(v, list)}
        return p
