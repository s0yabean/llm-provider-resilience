"""Fake providers for tests and the demo. No network, no keys.

A FakeProvider's behaviour is a list of outcomes consumed one per call, or a
mapping from secret to a fixed outcome. Each outcome is one of:

    "some text"      -> returned as the response
    401, 429, 503... -> raised as ProviderError with that status
    an exception     -> raised as-is (e.g. TimeoutError(), BrokenPipeError())
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from .credentials import fingerprint
from .errors import ProviderError

Outcome = Union[str, int, BaseException]


class FakeProvider:
    def __init__(
        self,
        name: str,
        script: Optional[List[Outcome]] = None,
        *,
        by_secret: Optional[Dict[str, Outcome]] = None,
        default: Outcome = "ok",
        retry_after: Optional[float] = None,
    ) -> None:
        self.name = name
        self.script = list(script or [])
        self.by_secret = by_secret or {}
        self.default = default
        self.retry_after = retry_after
        self.calls: List[dict] = []

    def complete(self, model: str, request: Any, secret: Optional[str]) -> Any:
        self.calls.append({"model": model, "key": fingerprint(secret) if secret else None})
        if secret is not None and secret in self.by_secret:
            outcome = self.by_secret[secret]
        elif self.script:
            outcome = self.script.pop(0)
        else:
            outcome = self.default
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, int):
            raise ProviderError(f"{self.name} returned HTTP {outcome}", status=outcome, retry_after=self.retry_after)
        return f"[{self.name}/{model}] {outcome}"
