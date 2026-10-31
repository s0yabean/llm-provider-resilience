"""Credential pools: several keys for one provider, with per-key health.

A key that returns 401/403 is marked exhausted and skipped until it is reset or
replaced. A key that returns 429 cools down for `retry_after` seconds. The pool
never prints a secret: repr and status() show the label and a short fingerprint.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from .clock import SystemClock
from .errors import ErrorKind

UNTESTED = "untested"
OK = "ok"
COOLING = "cooling"
EXHAUSTED = "exhausted"


def fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()[:8]


@dataclass
class Credential:
    label: str
    secret: str = field(repr=False)
    state: str = UNTESTED
    cooldown_until: float = 0.0
    last_error: Optional[str] = None
    uses: int = 0

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.secret)


class CredentialPool:
    """Hands out the next usable credential for one provider.

    strategy="fill_first" keeps using the first healthy key (predictable billing).
    strategy="round_robin" spreads load across healthy keys (spreads rate limits).
    """

    def __init__(
        self,
        credentials: Iterable[Credential],
        *,
        strategy: str = "fill_first",
        default_cooldown: float = 60.0,
        clock=None,
    ) -> None:
        if strategy not in ("fill_first", "round_robin"):
            raise ValueError(f"unknown strategy {strategy!r}")
        self.credentials: List[Credential] = list(credentials)
        self.strategy = strategy
        self.default_cooldown = default_cooldown
        self.clock = clock or SystemClock()
        self._cursor = 0

    @classmethod
    def from_env(cls, *names: str, environ=None, **kwargs) -> "CredentialPool":
        """Build a pool from environment variables. Unset or empty names are skipped."""
        env = os.environ if environ is None else environ
        creds = [Credential(label=n, secret=env[n]) for n in names if env.get(n)]
        return cls(creds, **kwargs)

    def _usable(self, cred: Credential) -> bool:
        if cred.state == EXHAUSTED:
            return False
        if cred.state == COOLING and self.clock.now() < cred.cooldown_until:
            return False
        return True

    def _effective_state(self, cred: Credential) -> str:
        if cred.state == COOLING and self.clock.now() >= cred.cooldown_until:
            return UNTESTED
        return cred.state

    def acquire(self, exclude: Iterable[str] = ()) -> Optional[Credential]:
        """Return the next usable credential, or None if every key is exhausted or cooling."""
        skip = set(exclude)
        n = len(self.credentials)
        if n == 0:
            return None
        start = self._cursor if self.strategy == "round_robin" else 0
        for i in range(n):
            idx = (start + i) % n
            cred = self.credentials[idx]
            if cred.label in skip or not self._usable(cred):
                continue
            if self.strategy == "round_robin":
                self._cursor = (idx + 1) % n
            cred.uses += 1
            return cred
        return None

    def report_success(self, cred: Credential) -> None:
        cred.state = OK
        cred.cooldown_until = 0.0
        cred.last_error = None

    def report_failure(self, cred: Credential, kind: ErrorKind, retry_after: Optional[float] = None) -> None:
        if kind == ErrorKind.AUTH:
            cred.state = EXHAUSTED
            cred.last_error = "auth"
        elif kind == ErrorKind.RATE_LIMIT:
            cred.state = COOLING
            cred.cooldown_until = self.clock.now() + (retry_after if retry_after is not None else self.default_cooldown)
            cred.last_error = "rate_limit"
        # Provider-level failures say nothing about the key; leave its state alone.

    def reset(self, label: Optional[str] = None) -> None:
        """Clear exhaustion/cooldown (for one label or all). Use after fixing the key."""
        for cred in self.credentials:
            if label is None or cred.label == label:
                cred.state = UNTESTED
                cred.cooldown_until = 0.0
                cred.last_error = None

    def replace(self, label: str, new_secret: str) -> None:
        """Swap in a new secret. A new fingerprint clears the old key's failure state."""
        for cred in self.credentials:
            if cred.label == label:
                if fingerprint(new_secret) != cred.fingerprint:
                    cred.secret = new_secret
                    self.reset(label)
                return
        self.credentials.append(Credential(label=label, secret=new_secret))

    def status(self) -> List[dict]:
        """Safe-to-log health table. Never includes the secret."""
        return [
            {"label": c.label, "fingerprint": c.fingerprint, "state": self._effective_state(c), "last_error": c.last_error}
            for c in self.credentials
        ]
