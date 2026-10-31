"""Fallback chain: try routes in order, rotate keys within a route, skip routes whose breaker is open.

Decision table for a failed call on one route:

    auth (401/403)        -> mark key exhausted, try the next key on the same route
    rate_limit (429)      -> cool the key down, try the next key on the same route
    overloaded / 5xx /
    timeout / connection  -> count a breaker failure, move to the next route
    bad_request (4xx)     -> stop; the request is wrong and another provider will not fix it

Only model-call failures go through this. Tool failures (a scraper erroring, a
file missing) are the agent's problem, not the provider's, and must not trigger
a provider switch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Protocol

from .breaker import CircuitBreaker
from .credentials import CredentialPool
from .errors import (
    CREDENTIAL_KINDS,
    AllRoutesFailed,
    ErrorKind,
    NonRetryableError,
    classify_exception,
)


class Provider(Protocol):
    """Anything that can make one model call. Adapters wrap a real SDK; tests use FakeProvider."""

    name: str

    def complete(self, model: str, request: Any, secret: Optional[str]) -> Any: ...


@dataclass
class Route:
    provider: Provider
    model: str
    pool: Optional[CredentialPool] = None  # None for providers that need no key (e.g. a local model)
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    name: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            self.name = f"{self.provider.name}:{self.model}"


@dataclass
class Attempt:
    route: str
    credential: Optional[str]
    outcome: str  # "ok", "circuit_open", "no_credentials", or an ErrorKind value
    detail: str = ""


@dataclass
class Result:
    response: Any
    route: str
    attempts: List[Attempt]

    @property
    def fell_back(self) -> bool:
        return any(a.outcome != "ok" for a in self.attempts)


_NO_RESPONSE = object()


class FallbackChain:
    def __init__(self, routes: List[Route], *, on_attempt: Optional[Callable[[Attempt], None]] = None) -> None:
        if not routes:
            raise ValueError("a chain needs at least one route")
        self.routes = routes
        self.on_attempt = on_attempt

    def _record(self, attempts: List[Attempt], attempt: Attempt) -> None:
        attempts.append(attempt)
        if self.on_attempt:
            self.on_attempt(attempt)

    def _try_route(self, route: Route, request: Any, attempts: List[Attempt]) -> Any:
        if not route.breaker.allow():
            self._record(attempts, Attempt(route.name, None, "circuit_open"))
            return _NO_RESPONSE

        tried: set = set()
        while True:
            cred = None
            if route.pool is not None:
                cred = route.pool.acquire(exclude=tried)
                if cred is None:
                    self._record(attempts, Attempt(route.name, None, "no_credentials"))
                    return _NO_RESPONSE
                tried.add(cred.label)

            label = cred.label if cred else None
            try:
                response = route.provider.complete(route.model, request, cred.secret if cred else None)
            except Exception as exc:  # adapters may raise SDK or network errors; classify them all
                err = classify_exception(exc)
                self._record(attempts, Attempt(route.name, label, err.kind.value, str(err)))
                if err.kind == ErrorKind.BAD_REQUEST:
                    raise NonRetryableError(route.name, err) from exc
                if err.kind in CREDENTIAL_KINDS and cred is not None:
                    route.pool.report_failure(cred, err.kind, err.retry_after)
                    continue  # next key, same route
                if err.kind not in CREDENTIAL_KINDS:
                    route.breaker.record_failure()
                return _NO_RESPONSE  # next route

            if cred is not None:
                route.pool.report_success(cred)
            route.breaker.record_success()
            self._record(attempts, Attempt(route.name, label, "ok"))
            return response

    def call(self, request: Any) -> Result:
        """Return the first successful response, or raise AllRoutesFailed with every attempt."""
        attempts: List[Attempt] = []
        for route in self.routes:
            response = self._try_route(route, request, attempts)
            if response is not _NO_RESPONSE:
                return Result(response=response, route=route.name, attempts=attempts)
        raise AllRoutesFailed(attempts)

    def preflight(self, probe_request: Any) -> List[dict]:
        """Probe every route independently before dispatching unattended work.

        A worker that fails its very first model call never reads its own
        fallback instructions, so check health at dispatch time instead.
        """
        report = []
        for route in self.routes:
            attempts: List[Attempt] = []
            try:
                ok = self._try_route(route, probe_request, attempts) is not _NO_RESPONSE
            except NonRetryableError:
                ok = False
            report.append({"route": route.name, "healthy": ok, "attempts": [a.outcome for a in attempts]})
        return report
