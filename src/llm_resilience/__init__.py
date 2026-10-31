"""Provider-agnostic LLM resilience: fallback chains, credential pools, circuit breakers."""

from .breaker import CircuitBreaker
from .chain import Attempt, FallbackChain, Provider, Result, Route
from .clock import FakeClock, SystemClock
from .credentials import Credential, CredentialPool
from .errors import AllRoutesFailed, ErrorKind, NonRetryableError, ProviderError, classify_status
from .worker import run_task

__all__ = [
    "AllRoutesFailed",
    "Attempt",
    "CircuitBreaker",
    "Credential",
    "CredentialPool",
    "ErrorKind",
    "FakeClock",
    "FallbackChain",
    "NonRetryableError",
    "Provider",
    "ProviderError",
    "Result",
    "Route",
    "SystemClock",
    "classify_status",
    "run_task",
]

__version__ = "0.1.0"
