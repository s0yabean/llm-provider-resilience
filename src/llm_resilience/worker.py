"""Exit protocol for unattended workers (cron jobs, queue workers, agent sub-tasks).

The failure this prevents: a worker hits a provider error, exits with code 0,
and never tells the scheduler. The task sits in "running" forever and nobody
notices until the output is missing hours later.

`run_task` guarantees exactly one of `complete` or `block` is called.
"""

from __future__ import annotations

from typing import Any, Callable

from .errors import AllRoutesFailed, NonRetryableError


def run_task(
    work: Callable[[], Any],
    *,
    complete: Callable[[Any], None],
    block: Callable[[str], None],
) -> bool:
    try:
        result = work()
    except AllRoutesFailed as exc:
        block(f"all providers failed: {exc}")
        return False
    except NonRetryableError as exc:
        block(f"request rejected: {exc}")
        return False
    except Exception as exc:
        block(f"worker error: {type(exc).__name__}: {exc}")
        return False
    complete(result)
    return True
