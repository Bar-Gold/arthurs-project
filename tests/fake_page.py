"""A stand-in for a Playwright Page that records everything done to it.

Lets the posting flow be tested without a browser or a Facebook account: the
step order, the halt-on-anomaly behaviour and the dry-run boundary are all
assertions over `page.calls`.

It implements only the surface GroupPoster actually uses. If the poster starts
calling something new, this fails loudly rather than silently passing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class FakeLocator:
    def __init__(
        self,
        page: "FakePage",
        description: str,
        *,
        matches: int = 1,
        wait_fails_for: tuple[str, ...] = (),
        never_detaches: bool = False,
    ) -> None:
        self.page = page
        self.description = description
        self.matches = matches
        self._wait_fails_for = wait_fails_for
        self._never_detaches = never_detaches

    # -- chaining ----------------------------------------------------------
    @property
    def first(self) -> "FakeLocator":
        return self

    def locator(self, selector: str) -> "FakeLocator":
        return self.page._resolve(f"{self.description} >> {selector}")

    def get_by_role(self, role: str, name: str | None = None, exact: bool = False) -> "FakeLocator":
        return self.page._resolve(f"{self.description} >> role={role}:{name}")

    def get_by_text(self, text: str, exact: bool = False) -> "FakeLocator":
        return self.page._resolve(f"{self.description} >> text={text}")

    # -- queries -----------------------------------------------------------
    def count(self) -> int:
        return self.matches

    # -- actions -----------------------------------------------------------
    def hover(self) -> None:
        self.page.calls.append(("hover", self.description))

    def click(self) -> None:
        self.page.calls.append(("click", self.description))
        handler = self.page.on_click.get(self.description)
        if handler is not None:
            handler()

    def wait_for(self, state: str = "visible", timeout: float = 0) -> None:
        self.page.calls.append(("wait_for", self.description, state))
        if self.description in self._wait_fails_for:
            raise TimeoutError(f"{self.description} never reached {state}")
        # A composer that opens fine but never closes -- what a slow connection
        # looks like. Only the detach wait fails.
        if self._never_detaches and state == "detached":
            raise TimeoutError(f"{self.description} never detached")
        # Waiting for something that is not there times out in Playwright too;
        # without this the fake would let verification pass on a missing post.
        if self.matches == 0 and state != "detached":
            raise TimeoutError(f"{self.description} not found")

    def set_input_files(self, files: Any) -> None:
        self.page.calls.append(("set_input_files", self.description, tuple(files)))


class FakeKeyboard:
    def __init__(self, page: "FakePage") -> None:
        self.page = page
        self.typed: list[str] = []

    def type(self, text: str) -> None:
        self.typed.append(text)
        self.page.calls.append(("type", text))

    def press(self, key: str) -> None:
        self.page.calls.append(("press", key))


class FakeMouse:
    def __init__(self, page: "FakePage") -> None:
        self.page = page

    def wheel(self, x: int, y: int) -> None:
        self.page.calls.append(("wheel", y))


@dataclass
class FakePage:
    """`missing` names never match; `body_text` drives anomaly detection."""

    url: str = "https://www.facebook.com/groups/testgroup/"
    # Where goto actually lands, when Facebook bounces us to a checkpoint or
    # the login page instead of the URL we asked for.
    redirect_to: str = ""
    body_text: str = "A normal looking group feed"
    missing: tuple[str, ...] = ()
    wait_fails_for: tuple[str, ...] = ()
    # The composer opens but never closes after Post is clicked.
    never_detaches: bool = False
    calls: list[tuple] = field(default_factory=list)
    on_click: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.keyboard = FakeKeyboard(self)
        self.mouse = FakeMouse(self)

    # -- helpers -----------------------------------------------------------
    def _resolve(self, description: str) -> FakeLocator:
        absent = any(token and token in description for token in self.missing)
        return FakeLocator(
            self,
            description,
            matches=0 if absent else 1,
            wait_fails_for=self.wait_fails_for,
            never_detaches=self.never_detaches,
        )

    @property
    def typed_text(self) -> str:
        return "".join(self.keyboard.typed)

    def clicked(self, needle: str) -> bool:
        return any(kind == "click" and needle in target for kind, target, *_ in self.calls)

    def call_names(self) -> list[str]:
        return [call[0] for call in self.calls]

    # -- Page surface ------------------------------------------------------
    def goto(self, url: str, timeout: float = 0, wait_until: str = "") -> None:
        self.calls.append(("goto", url))
        self.url = self.redirect_to or url

    def inner_text(self, selector: str) -> str:
        return self.body_text

    def wait_for_timeout(self, ms: float) -> None:
        # The poller in GroupPoster uses this while the SPA renders. Instant
        # here so the suite stays fast.
        self.calls.append(("wait_for_timeout", ms))

    def get_by_role(self, role: str, name: str | None = None, exact: bool = False) -> FakeLocator:
        return self._resolve(f"role={role}:{name}")

    def get_by_text(self, text: str, exact: bool = False) -> FakeLocator:
        return self._resolve(f"text={text}")

    def locator(self, selector: str) -> FakeLocator:
        return self._resolve(selector)
