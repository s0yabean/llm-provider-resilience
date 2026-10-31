"""Replay a bad day against fake providers. No network, no keys, no cost.

    python examples/simulate_outage.py

Scenario (synthetic): the primary provider's first key has expired, its second
key gets rate-limited, then the whole primary provider goes down for a while.
The chain keeps answering from the backup and returns to the primary after it
recovers.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm_resilience import CircuitBreaker, Credential, CredentialPool, FakeClock, FallbackChain, Route  # noqa: E402
from llm_resilience.fakes import FakeProvider  # noqa: E402

clock = FakeClock()

primary = FakeProvider(
    "primary",
    by_secret={"synthetic-expired-key": 401},
    script=["ok", 429, 503, 503],  # used by the healthy key; afterwards it answers normally
    retry_after=20,
)
backup = FakeProvider("backup")

chain = FallbackChain(
    [
        Route(
            primary,
            "large-model",
            CredentialPool(
                [Credential("PRIMARY_KEY_1", "synthetic-expired-key"), Credential("PRIMARY_KEY_2", "synthetic-good-key")],
                clock=clock,
            ),
            CircuitBreaker(failure_threshold=2, recovery_timeout=60, clock=clock),
        ),
        Route(backup, "backup-model", CredentialPool([Credential("BACKUP_KEY_1", "synthetic-backup-key")], clock=clock)),
    ]
)

served = {}
for minute in range(12):
    result = chain.call(f"request at t={minute * 10}s")
    served[result.route] = served.get(result.route, 0) + 1
    trail = " -> ".join(f"{a.route}[{a.credential or '-'}]:{a.outcome}" for a in result.attempts)
    print(f"t={minute * 10:>3}s  served by {result.route:<20} {trail}")
    clock.advance(10)

print()
print(f"requests answered: {sum(served.values())}/12")
for route, n in served.items():
    print(f"  {route}: {n}")
print(f"calls that reached the primary: {len(primary.calls)}")
print("primary key health:", chain.routes[0].pool.status())
