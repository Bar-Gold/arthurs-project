"""A stuck tab in the app's Chrome: noticed, closed, and nothing else touched.

Nothing here opens Chrome. The DevTools client is tested against a WebSocket
server on a thread of its own; the sweep and the restart against a pretend
browser that answers, or does not, exactly as tabs.py was verified live to
see a real one do (see the module docstring).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from fbposter import session, tabs
from fbposter.cdp import CdpClient, CdpError

PROFILE = Path(r"C:\FBAutomation\ChromeProfile")
VERSION = {"Browser": "Chrome/153", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/x"}
OURS = ["chrome.exe", "--remote-debugging-port=9222", f"--user-data-dir={PROFILE}"]


# -- a WebSocket server, for the client ----------------------------------------
class Server:
    """Accepts one connection and answers each request with `handler(msg)`.

    `handler` returns a list of messages to send back (replies and events),
    or [] to stay silent.
    """

    def __init__(self, handler, accept_key=None) -> None:
        self.handler = handler
        self.accept_key = accept_key
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received: list[dict] = []
        self.pongs: list[bytes] = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/devtools/browser/x"

    def _serve(self) -> None:
        conn, _ = self.listener.accept()
        self.conn = conn
        head = b""
        while b"\r\n\r\n" not in head:
            head += conn.recv(1)
        key = next(line.split(":", 1)[1].strip() for line in head.decode().split("\r\n")
                   if line.lower().startswith("sec-websocket-key"))
        accept = self.accept_key or base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        try:
            while True:
                opcode, payload = self._read(conn)
                if opcode == 0xA:
                    self.pongs.append(payload)
                    continue
                message = json.loads(payload)
                self.received.append(message)
                for out in self.handler(message):
                    if out == "PING":
                        self.send(b"hello", opcode=0x9)
                    else:
                        self.send(json.dumps(out).encode())
        except (OSError, ValueError):
            pass

    def send(self, payload: bytes, opcode: int = 0x1) -> None:
        header = bytes([0x80 | opcode])
        if len(payload) < 126:
            header += bytes([len(payload)])
        elif len(payload) < 1 << 16:
            header += bytes([126]) + struct.pack(">H", len(payload))
        else:
            header += bytes([127]) + struct.pack(">Q", len(payload))
        self.conn.sendall(header + payload)

    @staticmethod
    def _read(conn) -> tuple[int, bytes]:
        def exact(n):
            data = b""
            while len(data) < n:
                chunk = conn.recv(n - len(data))
                if not chunk:
                    raise OSError("closed")
                data += chunk
            return data

        first, second = exact(2)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", exact(2))
        elif length == 127:
            (length,) = struct.unpack(">Q", exact(8))
        mask = exact(4)
        assert second & 0x80, "a client frame must be masked"
        data = exact(length)
        return first & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(data))


def reply(message, result=None):
    return {"id": message["id"], "result": result or {}}


class TestTheClient:
    def test_a_call_gets_its_own_reply(self):
        server = Server(lambda m: [reply(m, {"echo": m["method"]})])
        with CdpClient.open(server.url) as client:
            assert client.call("Target.getTargets") == {"echo": "Target.getTargets"}
            assert client.call("Browser.getVersion") == {"echo": "Browser.getVersion"}

    def test_events_in_between_are_ignored(self):
        server = Server(lambda m: [{"method": "Target.targetCreated", "params": {}}, reply(m, {"ok": 1})])
        with CdpClient.open(server.url) as client:
            assert client.call("Target.getTargets") == {"ok": 1}

    def test_the_session_is_sent(self):
        server = Server(lambda m: [reply(m)])
        with CdpClient.open(server.url) as client:
            client.call("Runtime.getIsolateId", session="S1")
        assert server.received[0]["sessionId"] == "S1"

    def test_an_error_reply_raises(self):
        server = Server(lambda m: [{"id": m["id"], "error": {"message": "No target with given id"}}])
        with CdpClient.open(server.url) as client, pytest.raises(CdpError, match="No target"):
            client.call("Target.closeTarget", {"targetId": "gone"})

    def test_silence_is_a_timeout_not_a_hang(self):
        """The whole point: a question a stuck tab never answers must end."""
        server = Server(lambda m: [])
        with CdpClient.open(server.url) as client, pytest.raises(TimeoutError):
            client.call("Runtime.getIsolateId", timeout=0.3)

    def test_collect_returns_only_what_answered_in_time(self):
        server = Server(lambda m: [reply(m)] if m["params"].get("answer") else [])
        with CdpClient.open(server.url) as client:
            yes = client.post("X", {"answer": True})
            no = client.post("X", {"answer": False})
            also = client.post("X", {"answer": True})
            got = client.collect([yes, no, also], timeout=0.5)
        assert set(got) == {yes, also}

    def test_a_ping_is_answered(self):
        server = Server(lambda m: ["PING", reply(m)])
        with CdpClient.open(server.url) as client:
            client.call("X")
            # The pong is sent before the reply is read, but the server's
            # thread records it in its own time.
            deadline = time.monotonic() + 5
            while not server.pongs and time.monotonic() < deadline:
                time.sleep(0.01)
        assert server.pongs == [b"hello"]

    def test_a_long_message_is_read_whole(self):
        big = {"targetInfos": [{"targetId": str(i), "type": "page", "url": "x" * 100} for i in range(800)]}
        server = Server(lambda m: [reply(m, big)])
        with CdpClient.open(server.url) as client:
            assert client.call("Target.getTargets") == big

    def test_a_handshake_that_does_not_match_is_refused(self):
        server = Server(lambda m: [], accept_key="wrong")
        with pytest.raises(CdpError, match="did not match"):
            CdpClient.open(server.url)

    def test_it_never_connects_off_this_machine(self):
        with pytest.raises(CdpError, match="this machine"):
            CdpClient.open("ws://192.168.1.20:9222/devtools/browser/x")

    def test_nothing_listening_is_a_cdp_error(self):
        spare = socket.socket()
        spare.bind(("127.0.0.1", 0))
        port = spare.getsockname()[1]
        spare.close()
        with pytest.raises(CdpError):
            CdpClient.open(f"ws://127.0.0.1:{port}/devtools/browser/x", timeout=1)


# -- whose Chrome is it ---------------------------------------------------------
class TestOnlyTheAppsOwnChrome:
    """Asked for in so many words: the app watches its own background Chrome
    and nothing else on the client's laptop."""

    def test_the_apps_profile_is_recognised(self):
        assert tabs.started_on(OURS, PROFILE)

    @pytest.mark.parametrize("arg", [
        r"--user-data-dir=c:\fbautomation\chromeprofile",
        r"--user-data-dir=C:\FBAutomation\ChromeProfile\\",
        r'--user-data-dir="C:\FBAutomation\ChromeProfile"',
        r"--USER-DATA-DIR=C:\FBAutomation\ChromeProfile",
    ])
    def test_spelling_differences_do_not_matter(self, arg):
        assert tabs.started_on(["chrome.exe", arg], PROFILE)

    @pytest.mark.parametrize("argv", [
        ["chrome.exe"],  # the everyday profile: no --user-data-dir at all
        ["chrome.exe", r"--user-data-dir=C:\Users\Client\AppData\Local\Google\Chrome\User Data"],
        ["chrome.exe", r"--user-data-dir=C:\FBAutomation\ChromeProfile2"],
        ["chrome.exe", r"--user-data-dir=C:\FBAutomation"],
        ["chrome.exe", "--user-data-dir="],
    ])
    def test_any_other_chrome_is_not_the_apps(self, argv):
        assert not tabs.started_on(argv, PROFILE)

    def test_a_profile_path_with_spaces(self):
        profile = Path(r"C:\Users\John Smith\AppData\Local\FBAutomation\ChromeProfile")
        assert tabs.started_on(["chrome.exe", f"--user-data-dir={profile}"], profile)

    @pytest.mark.skipif(sys.platform != "win32", reason="reads a Windows process")
    def test_a_real_command_line_is_read_from_windows(self):
        """Read off this very test process: the Python running it."""
        argv = tabs.command_line(os.getpid())
        assert argv is not None
        assert Path(argv[0]).name.lower().startswith("python")

    @pytest.mark.skipif(sys.platform != "win32", reason="reads a Windows process")
    def test_a_process_that_is_not_there_reads_as_nothing(self):
        assert tabs.command_line(0x7FFFFFF0) is None


# -- a pretend Chrome, for the sweep and the restart ---------------------------
class FakeChrome:
    """Answers like the real one did live: the browser always, a tab only
    while it is not stuck."""

    def __init__(self, pages, argv=OURS, still_stuck_on_second_look=True):
        self.pages = {p["targetId"]: dict(p) for p in pages}
        self.argv = argv
        self.second_look_stuck = still_stuck_on_second_look
        self.looks: dict[str, int] = {}
        self.calls: list[str] = []
        self.closed: list[str] = []
        self.created: list[str] = []
        self.shut = False
        self._sessions: dict[str, str] = {}
        self._posted: dict[int, str] = {}
        self._next = 0

    # CdpClient's surface
    def open(self, _url):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        pass

    def call(self, method, params=None, session=None, timeout=5.0):
        self.calls.append(method)
        params = params or {}
        if method == "SystemInfo.getProcessInfo":
            return {"processInfo": [{"type": "browser", "id": 4242}, {"type": "renderer", "id": 1}]}
        if method == "Target.getTargets":
            return {"targetInfos": [dict(p, type=p.get("type", "page")) for p in self.pages.values()]}
        if method == "Target.attachToTarget":
            target = params["targetId"]
            if target not in self.pages:
                raise CdpError("No target with given id found")
            self._sessions[f"S-{target}"] = target
            return {"sessionId": f"S-{target}"}
        if method == "Target.detachFromTarget":
            return {}
        if method == "Target.closeTarget":
            target = params["targetId"]
            if target not in self.pages:
                raise CdpError("No target with given id found")
            self.closed.append(target)
            del self.pages[target]
            return {"success": True}
        if method == "Target.createTarget":
            self.created.append(params["url"])
            self.pages["blank"] = {"targetId": "blank", "url": params["url"], "title": ""}
            return {"targetId": "blank"}
        if method == "Browser.close":
            self.shut = True
            raise CdpError("Chrome closed the connection.")
        raise AssertionError(f"unexpected {method}")

    def post(self, method, params=None, session=None):
        assert method == "Runtime.getIsolateId", "a tab is asked nothing else"
        self.calls.append(method)
        self._next += 1
        self._posted[self._next] = self._sessions[session]
        return self._next

    def collect(self, ids, timeout):
        answered = {}
        for i in ids:
            target = self._posted[i]
            look = self.looks[target] = self.looks.get(target, 0) + 1
            page = self.pages.get(target, {})
            stuck = page.get("stuck") and (look == 1 or self.second_look_stuck)
            if not stuck:
                answered[i] = {"id": i, "result": {"id": "isolate"}}
        return answered

    def command_line(self, pid):
        assert pid == 4242, "must read the *browser* process"
        return self.argv


def page(target, stuck=False, title=None):
    return {"targetId": target, "url": f"https://example.com/{target}", "title": title or target,
            "stuck": stuck}


def sweep(fake, slept=None):
    return tabs.clear_stuck_tabs(
        profile_dir=PROFILE, open_client=fake.open, command_line_of=fake.command_line,
        probe=lambda: VERSION, sleep=(slept.append if slept is not None else lambda s: None),
    )


class TestTheSweep:
    def test_a_healthy_chrome_loses_nothing_and_waits_for_nothing(self):
        fake = FakeChrome([page("a"), page("b")])
        slept = []
        assert sweep(fake, slept) == []
        assert fake.closed == [] and slept == []

    def test_a_stuck_tab_is_closed(self):
        fake = FakeChrome([page("feed"), page("login", stuck=True, title="Facebook")])
        assert sweep(fake) == ["Facebook"]
        assert fake.closed == ["login"]

    def test_only_after_a_second_look(self):
        """A page merely busy loading -- Facebook on a slow laptop -- answers
        the second time, and must not be taken for stuck."""
        fake = FakeChrome([page("slow", stuck=True)], still_stuck_on_second_look=False)
        slept = []
        assert sweep(fake, slept) == []
        assert fake.closed == []
        assert slept == [tabs.SECOND_LOOK_S]

    def test_the_last_tab_is_never_closed_without_a_blank_one_first(self):
        """Chrome quits when its last tab closes."""
        fake = FakeChrome([page("only", stuck=True)])
        sweep(fake)
        assert fake.created == ["about:blank"]
        assert fake.calls.index("Target.createTarget") < fake.calls.index("Target.closeTarget")
        assert list(fake.pages) == ["blank"]

    def test_a_tab_the_user_closed_meanwhile_is_not_an_error(self):
        fake = FakeChrome([page("a", stuck=True), page("b", stuck=True), page("c")])
        real_close = fake.call

        def call(method, params=None, session=None, timeout=5.0):
            if method == "Target.closeTarget" and params["targetId"] == "a":
                fake.pages.pop("a", None)  # gone by the time it is closed
            return real_close(method, params, session, timeout)

        fake.call = call
        assert sweep(fake) == ["b"]

    def test_only_pages_are_looked_at(self):
        fake = FakeChrome([page("a"), dict(page("worker", stuck=True), type="service_worker")])
        assert sweep(fake) == []
        assert "worker" not in fake.looks

    def test_a_chrome_on_another_profile_is_asked_nothing_about_its_tabs(self):
        fake = FakeChrome([page("x", stuck=True)],
                          argv=["chrome.exe", r"--user-data-dir=C:\Somewhere\Else"])
        assert sweep(fake) == []
        assert fake.calls == ["SystemInfo.getProcessInfo"]
        assert fake.closed == []

    def test_a_command_line_that_cannot_be_read_counts_as_not_ours(self):
        fake = FakeChrome([page("x", stuck=True)])
        fake.argv = None
        assert sweep(fake) == [] and fake.closed == []

    def test_no_chrome_no_look(self):
        opened = []
        assert tabs.clear_stuck_tabs(open_client=opened.append, probe=lambda: None) == []
        assert opened == []

    def test_it_never_raises(self):
        """It runs in front of every connection; failing to look must never
        be what stops one."""
        def broken(_url):
            raise CdpError("refused")

        assert tabs.clear_stuck_tabs(open_client=broken, probe=lambda: VERSION) == []

    def test_nothing_is_touched_while_the_worker_has_a_page_open(self):
        fake = FakeChrome([page("post", stuck=True)])
        with tabs.posting():
            assert tabs.posting_now()
            assert sweep(fake) == []
        assert fake.calls == [] and fake.closed == []
        assert not tabs.posting_now()


def restart(fake, launched, *, alive_after=0, profile_busy=lambda: False):
    """restart_chrome against the pretend browser. Chrome is taken to be down
    once it has been told to close and `alive_after` more probes have gone by."""
    probes = {"after_close": 0}

    def probe():
        if not fake.shut:
            return VERSION
        probes["after_close"] += 1
        return VERSION if probes["after_close"] <= alive_after else None

    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    return tabs.restart_chrome(
        profile_dir=PROFILE, launch=lambda: launched.append(1), open_client=fake.open,
        command_line_of=fake.command_line, probe=probe, profile_busy=profile_busy,
        sleep=sleep, monotonic=lambda: clock["now"],
    )


class TestTheLastResort:
    def test_the_apps_chrome_is_closed_and_started_again(self):
        fake, launched = FakeChrome([page("stuck", stuck=True)]), []
        assert restart(fake, launched, alive_after=3) == tabs.RESTARTED
        assert fake.shut and launched == [1]

    def test_it_waits_for_the_profile_to_be_let_go(self):
        """Launching while the old one still holds the profile would hand the
        launch to it (see chrome.profile_in_use)."""
        fake, launched = FakeChrome([page("a")]), []
        held = iter([True, True, False])
        assert restart(fake, launched, profile_busy=lambda: next(held, False)) == tabs.RESTARTED
        assert launched == [1]

    def test_a_chrome_that_will_not_close_is_not_started_over(self):
        fake, launched = FakeChrome([page("a")]), []
        assert restart(fake, launched, alive_after=10_000) == tabs.WOULD_NOT_CLOSE
        assert launched == []

    def test_a_chrome_on_another_profile_is_never_closed(self):
        fake, launched = FakeChrome([page("a")], argv=["chrome.exe"]), []
        assert restart(fake, launched) == tabs.NOT_OURS
        assert not fake.shut and launched == []
        assert "Browser.close" not in fake.calls

    def test_never_mid_post(self):
        fake, launched = FakeChrome([page("a")]), []
        with tabs.posting():
            assert restart(fake, launched) == tabs.POSTING
        assert not fake.shut and fake.calls == []

    def test_nothing_to_restart_when_it_has_gone(self):
        launched = []
        outcome = tabs.restart_chrome(profile_dir=PROFILE, launch=lambda: launched.append(1),
                                      probe=lambda: None, profile_busy=lambda: False)
        assert outcome == tabs.GONE and launched == []


# -- where it runs --------------------------------------------------------------
class TestItRunsBeforeEveryConnection:
    def test_session_attach_runs_it_first(self, monkeypatch):
        order = []
        monkeypatch.setattr(session, "before_attach", lambda: order.append("sweep"))

        class Stop(Exception):
            pass

        import playwright.sync_api as sync_api

        def sync_playwright():
            order.append("connect")
            raise Stop

        monkeypatch.setattr(sync_api, "sync_playwright", sync_playwright)
        with pytest.raises(Stop):
            with session.attach("http://127.0.0.1:1"):
                pass
        assert order == ["sweep", "connect"]

    def test_a_sweep_that_fails_does_not_stop_the_connection(self, monkeypatch):
        def broken():
            raise RuntimeError("looking went wrong")

        monkeypatch.setattr(session, "before_attach", broken)
        import playwright.sync_api as sync_api

        reached = []

        def sync_playwright():
            reached.append(1)
            raise ConnectionError

        monkeypatch.setattr(sync_api, "sync_playwright", sync_playwright)
        with pytest.raises(ConnectionError):
            with session.attach("http://127.0.0.1:1"):
                pass
        assert reached == [1]

    def test_inert_unless_the_real_app_sets_it(self):
        """The suite never sets it, so no test can reach into -- let alone
        close a tab in -- a Chrome running on the developer's machine."""
        assert session.before_attach is None

    def test_the_real_app_sets_it(self):
        import inspect

        from fbposter.qtui import app as qt_app

        source = inspect.getsource(qt_app.run)
        assert "session.before_attach = tabs.clear_stuck_tabs" in source
        assert "window._clear_stuck_tabs = lambda: tabs.clear_stuck_tabs()" in source
        assert "window._restart_chrome = lambda: tabs.restart_chrome()" in source


class TestTheWorkersPagesAreMarked:
    """Marked only once connected: a worker hung *in* the connect has no page
    open, and must not stop the sweep that would free it."""

    def test_a_post_holds_the_mark_and_lets_it_go(self, monkeypatch):
        from fbposter import worker

        seen = {}

        class Page:
            def close(self):
                seen["closed_while_marked"] = tabs.posting_now()

        class Context:
            def new_page(self):
                seen["marked_at_new_page"] = tabs.posting_now()
                return Page()

        @contextmanager
        def attach(endpoint=None):
            seen["marked_during_connect"] = tabs.posting_now()
            yield Context()

        class Poster:
            def __init__(self, page, dry_run=False):
                pass

            def post(self, request):
                seen["marked_while_posting"] = tabs.posting_now()
                return "outcome"

        monkeypatch.setattr(session, "attach", attach)
        monkeypatch.setattr(worker, "GroupPoster", Poster)
        assert worker.LivePoster().post(object()) == "outcome"
        assert seen == {"marked_during_connect": False, "marked_at_new_page": True,
                        "marked_while_posting": True, "closed_while_marked": True}
        assert not tabs.posting_now()
