"""Error classification: decide what a provider failure means before deciding what to do."""

from __future__ import annotations

import enum
from typing import Optional


class ErrorKind(str, enum.Enum):
    AUTH = "auth"                # 401/403: key expired, revoked or wrong. Rotating to another key may help.
    RATE_LIMIT = "rate_limit"    # 429: this key is throttled. Another key or provider may help.
    OVERLOADED = "overloaded"    # 529: the provider is shedding load. Another provider will help.
    UNAVAILABLE = "unavailable"  # 500/502/503/504: provider-side fault.
    TIMEOUT = "timeout"          # the call hung past the client timeout.
    CONNECTION = "connection"    # DNS failure, reset, broken pipe.
    BAD_REQUEST = "bad_request"  # 400/404/422: the request itself is wrong. Retrying elsewhere rarely helps.
    UNKNOWN = "unknown"


# Failures that say "this credential is unusable" (stay on the provider, try the next key).
CREDENTIAL_KINDS = frozenset({ErrorKind.AUTH, ErrorKind.RATE_LIMIT})

# Failures that say "this provider is unhealthy" (count toward the circuit breaker, move on).
PROVIDER_KINDS = frozenset(
    {ErrorKind.OVERLOADED, ErrorKind.UNAVAILABLE, ErrorKind.TIMEOUT, ErrorKind.CONNECTION, ErrorKind.UNKNOWN}
)


class ProviderError(Exception):
    """Raised by a provider adapter when a model call fails.

    Adapters translate their SDK's exceptions into this one, so the chain never
    needs to know which SDK it is talking to.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        kind: Optional[ErrorKind] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind or classify_status(status)
        self.retry_after = retry_after


class AllRoutesFailed(Exception):
    """Every route in the chain was skipped or failed. `attempts` says why, in order."""

    def __init__(self, attempts: list) -> None:
        self.attempts = attempts
        summary = "; ".join(f"{a.route}: {a.outcome}" for a in attempts) or "no routes configured"
        super().__init__(f"all routes failed ({summary})")


class NonRetryableError(Exception):
    """The request was rejected for a reason another provider would not fix."""

    def __init__(self, route: str, cause: ProviderError) -> None:
        self.route = route
        self.cause = cause
        super().__init__(f"{route} rejected the request: {cause}")


def classify_status(status: Optional[int]) -> ErrorKind:
    if status is None:
        return ErrorKind.UNKNOWN
    if status in (401, 403):
        return ErrorKind.AUTH
    if status == 429:
        return ErrorKind.RATE_LIMIT
    if status == 529:
        return ErrorKind.OVERLOADED
    if status in (408,):
        return ErrorKind.TIMEOUT
    if 500 <= status < 600:
        return ErrorKind.UNAVAILABLE
    if 400 <= status < 500:
        return ErrorKind.BAD_REQUEST
    return ErrorKind.UNKNOWN


def classify_exception(exc: BaseException) -> ProviderError:
    """Map built-in network exceptions to ProviderError so adapters can let them propagate."""
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, TimeoutError):
        return ProviderError(str(exc) or "timed out", kind=ErrorKind.TIMEOUT)
    if isinstance(exc, (ConnectionError, BrokenPipeError, OSError)):
        return ProviderError(str(exc) or "connection failed", kind=ErrorKind.CONNECTION)
    return ProviderError(f"{type(exc).__name__}: {exc}", kind=ErrorKind.UNKNOWN)
