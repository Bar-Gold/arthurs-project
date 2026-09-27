"""A small DevTools client on the standard library alone.

Playwright is the app's way into Chrome, and it is also the thing that hangs:
its connect waits, for ever, on any tab that has stopped answering (see
`tabs.py`). Noticing and clearing such a tab therefore needs a way in that
does not go through Playwright, and this is it -- one WebSocket to the
browser, a request, a reply.

It speaks only to the debugging port on 127.0.0.1, and only what `tabs.py`
needs. It is deliberately not a general client: no events are kept, and
nothing here enables a DevTools domain, so a page it looks at runs nothing it
would not have run anyway.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import time
import urllib.parse
from typing import Any

# How long a reply may take by default. Everything this client asks is
# answered by the browser process itself in milliseconds, except the one
# question put to a tab -- and that one passes its own, shorter, limit.
CALL_TIMEOUT_S = 5.0
# Once a frame has started to arrive, the rest of it comes straight after.
# Separate from the caller's deadline so that a deadline can only ever expire
# *between* frames, never half-way through one -- which would leave the
# connection unreadable.
FRAME_TIMEOUT_S = 5.0

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class CdpError(Exception):
    """Chrome answered with an error, or the connection did not work."""


class CdpClient:
    """One WebSocket to Chrome. Requests are numbered; replies are matched."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._next_id = 0
        self._replies: dict[int, dict[str, Any]] = {}

    @classmethod
    def open(cls, ws_url: str, timeout: float = CALL_TIMEOUT_S) -> "CdpClient":
        parts = urllib.parse.urlparse(ws_url)
        if parts.hostname not in ("127.0.0.1", "localhost"):
            raise CdpError(f"Refusing to connect anywhere but this machine: {ws_url}")
        try:
            sock = socket.create_connection((parts.hostname, parts.port), timeout=timeout)
        except OSError as exc:
            raise CdpError(f"Could not reach Chrome at {ws_url}: {exc}") from exc
        try:
            _handshake(sock, parts, timeout)
        except BaseException:
            sock.close()
            raise
        return cls(sock)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> "CdpClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- asking -----------------------------------------------------------
    def post(self, method: str, params: dict | None = None,
             session: str | None = None) -> int:
        """Send a request without waiting for it. Returns its id."""
        self._next_id += 1
        message: dict[str, Any] = {"id": self._next_id, "method": method,
                                   "params": params or {}}
        if session is not None:
            message["sessionId"] = session
        self._send_frame(json.dumps(message).encode("utf-8"))
        return self._next_id

    def collect(self, ids: list[int], timeout: float) -> dict[int, dict[str, Any]]:
        """The replies to `ids` that arrive within `timeout`. Missing = silent."""
        wanted = set(ids)
        deadline = time.monotonic() + timeout
        while not wanted <= self._replies.keys():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                self._read_message(remaining)
            except TimeoutError:
                break
        return {i: self._replies.pop(i) for i in ids if i in self._replies}

    def call(self, method: str, params: dict | None = None,
             session: str | None = None, timeout: float = CALL_TIMEOUT_S) -> dict[str, Any]:
        """Send a request and return its result. Raises on an error or silence."""
        request = self.post(method, params, session)
        reply = self.collect([request], timeout).get(request)
        if reply is None:
            raise TimeoutError(f"Chrome did not answer {method} within {timeout:.0f}s.")
        if "error" in reply:
            raise CdpError(f"{method}: {reply['error'].get('message', reply['error'])}")
        return reply.get("result", {})

    # -- the wire ---------------------------------------------------------
    def _read_message(self, timeout: float) -> None:
        """Read one whole message and keep it if it is a reply."""
        data = b""
        while True:
            opcode, fin, payload = self._read_frame(timeout)
            if opcode == 0x8:
                raise CdpError("Chrome closed the connection.")
            if opcode == 0x9:  # ping: answer it and keep reading
                self._send_frame(payload, opcode=0xA)
                continue
            if opcode == 0xA:
                continue
            data += payload
            if fin:
                break
        try:
            message = json.loads(data)
        except ValueError:
            return
        if isinstance(message, dict) and isinstance(message.get("id"), int):
            self._replies[message["id"]] = message
        # Anything else is an event, and this client keeps none.

    def _read_frame(self, timeout: float) -> tuple[int, bool, bytes]:
        self._sock.settimeout(timeout)
        try:
            first = self._sock.recv(1)
        except socket.timeout as exc:
            raise TimeoutError("no reply yet") from exc
        if not first:
            raise CdpError("Chrome closed the connection.")
        self._sock.settimeout(FRAME_TIMEOUT_S)
        try:
            (second,) = self._recv_exact(1)
            length = second & 0x7F
            if length == 126:
                (length,) = struct.unpack(">H", self._recv_exact(2))
            elif length == 127:
                (length,) = struct.unpack(">Q", self._recv_exact(8))
            mask = self._recv_exact(4) if second & 0x80 else b""
            payload = self._recv_exact(length)
        except socket.timeout as exc:
            raise CdpError("Chrome stopped part-way through a message.") from exc
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return first[0] & 0x0F, bool(first[0] & 0x80), payload

    def _recv_exact(self, count: int) -> bytes:
        data = b""
        while len(data) < count:
            chunk = self._sock.recv(count - len(data))
            if not chunk:
                raise CdpError("Chrome closed the connection.")
            data += chunk
        return data

    def _send_frame(self, payload: bytes, opcode: int = 0x1) -> None:
        # A client must mask what it sends (RFC 6455, 5.3).
        mask = os.urandom(4)
        header = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 1 << 16:
            header += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", length)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        try:
            self._sock.sendall(header + mask + masked)
        except OSError as exc:
            raise CdpError(f"Could not send to Chrome: {exc}") from exc


def _handshake(sock: socket.socket, parts: urllib.parse.ParseResult, timeout: float) -> None:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    host = f"{parts.hostname}:{parts.port}"
    path = parts.path or "/"
    request = (
        f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.settimeout(timeout)
    try:
        sock.sendall(request.encode("ascii"))
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(1)
            if not chunk:
                raise CdpError("Chrome closed the connection during the handshake.")
            head += chunk
            if len(head) > 16_384:
                raise CdpError("Chrome's handshake reply was not a WebSocket upgrade.")
    except socket.timeout as exc:
        raise CdpError("Chrome did not answer the handshake.") from exc
    except OSError as exc:
        raise CdpError(f"The handshake with Chrome failed: {exc}") from exc

    lines = head.decode("latin-1").split("\r\n")
    if " 101 " not in f"{lines[0]} ":
        raise CdpError(f"Chrome refused the connection: {lines[0]}")
    expected = base64.b64encode(
        hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
    ).decode("ascii")
    accept = next(
        (line.split(":", 1)[1].strip() for line in lines
         if line.lower().startswith("sec-websocket-accept:")),
        None,
    )
    if accept != expected:
        raise CdpError("Chrome's handshake reply did not match the request.")
