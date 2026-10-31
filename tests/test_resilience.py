import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm_resilience import (  # noqa: E402
    AllRoutesFailed,
    CircuitBreaker,
    Credential,
    CredentialPool,
    ErrorKind,
    FakeClock,
    FallbackChain,
    NonRetryableError,
    ProviderError,
    Route,
    classify_status,
    run_task,
)
from llm_resilience.fakes import FakeProvider  # noqa: E402

# All secrets below are synthetic placeholders.
KEY_A, KEY_B = "test-key-a", "test-key-b"


def pool(*secrets, clock=None, **kw):
    return CredentialPool(
        [Credential(label=f"key{i}", secret=s) for i, s in enumerate(secrets)], clock=clock or FakeClock(), **kw
    )


class ClassifyTest(unittest.TestCase):
    def test_status_codes(self):
        self.assertEqual(classify_status(401), ErrorKind.AUTH)
        self.assertEqual(classify_status(403), ErrorKind.AUTH)
        self.assertEqual(classify_status(429), ErrorKind.RATE_LIMIT)
        self.assertEqual(classify_status(529), ErrorKind.OVERLOADED)
        self.assertEqual(classify_status(503), ErrorKind.UNAVAILABLE)
        self.assertEqual(classify_status(400), ErrorKind.BAD_REQUEST)
        self.assertEqual(classify_status(None), ErrorKind.UNKNOWN)


class CredentialPoolTest(unittest.TestCase):
    def test_auth_failure_exhausts_key_until_reset(self):
        p = pool(KEY_A, KEY_B)
        a = p.acquire()
        p.report_failure(a, ErrorKind.AUTH)
        self.assertEqual(p.acquire().label, "key1")
        p.report_failure(p.credentials[1], ErrorKind.AUTH)
        self.assertIsNone(p.acquire())
        p.reset("key0")
        self.assertEqual(p.acquire().label, "key0")

    def test_rate_limit_cools_down_then_recovers(self):
        clock = FakeClock()
        p = pool(KEY_A, clock=clock)
        p.report_failure(p.acquire(), ErrorKind.RATE_LIMIT, retry_after=10)
        self.assertIsNone(p.acquire())
        clock.advance(10)
        self.assertEqual(p.acquire().label, "key0")

    def test_replace_with_new_secret_clears_exhaustion(self):
        p = pool(KEY_A)
        p.report_failure(p.acquire(), ErrorKind.AUTH)
        p.replace("key0", KEY_A)  # same secret: still dead
        self.assertIsNone(p.acquire())
        p.replace("key0", "test-key-a-rotated")
        self.assertIsNotNone(p.acquire())

    def test_round_robin_spreads_load(self):
        p = pool(KEY_A, KEY_B, strategy="round_robin")
        self.assertEqual([p.acquire().label for _ in range(4)], ["key0", "key1", "key0", "key1"])

    def test_from_env_skips_unset_names(self):
        p = CredentialPool.from_env("X_KEY_1", "X_KEY_2", environ={"X_KEY_1": KEY_A, "X_KEY_2": ""})
        self.assertEqual([c.label for c in p.credentials], ["X_KEY_1"])

    def test_secret_never_in_repr_or_status(self):
        p = pool(KEY_A)
        self.assertNotIn(KEY_A, repr(p.credentials[0]))
        self.assertNotIn(KEY_A, str(p.status()))


class CircuitBreakerTest(unittest.TestCase):
    def test_opens_after_threshold_and_half_opens_after_timeout(self):
        clock = FakeClock()
        b = CircuitBreaker(failure_threshold=2, recovery_timeout=30, clock=clock)
        b.record_failure()
        self.assertTrue(b.allow())
        b.record_failure()
        self.assertEqual(b.state, "open")
        self.assertFalse(b.allow())
        clock.advance(30)
        self.assertEqual(b.state, "half_open")
        b.record_failure()  # trial call failed: straight back to open
        self.assertEqual(b.state, "open")
        clock.advance(30)
        b.record_success()
        self.assertEqual(b.state, "closed")


class FallbackChainTest(unittest.TestCase):
    def test_primary_ok_no_fallback(self):
        primary = FakeProvider("primary")
        chain = FallbackChain([Route(primary, "m1", pool(KEY_A))])
        r = chain.call("hi")
        self.assertEqual(r.route, "primary:m1")
        self.assertFalse(r.fell_back)

    def test_expired_key_rotates_within_provider(self):
        primary = FakeProvider("primary", by_secret={KEY_A: 401, KEY_B: "fine"})
        backup = FakeProvider("backup")
        chain = FallbackChain([Route(primary, "m1", pool(KEY_A, KEY_B)), Route(backup, "m2")])
        r = chain.call("hi")
        self.assertEqual(r.route, "primary:m1")
        self.assertEqual([a.outcome for a in r.attempts], ["auth", "ok"])
        self.assertEqual(backup.calls, [])

    def test_all_keys_dead_falls_to_next_provider(self):
        primary = FakeProvider("primary", default=401)
        backup = FakeProvider("backup")
        chain = FallbackChain([Route(primary, "m1", pool(KEY_A, KEY_B)), Route(backup, "m2", pool("test-key-c"))])
        r = chain.call("hi")
        self.assertEqual(r.route, "backup:m2")
        self.assertEqual([a.outcome for a in r.attempts], ["auth", "auth", "no_credentials", "ok"])
        # Second call skips the dead keys without calling the primary again.
        before = len(primary.calls)
        chain.call("hi")
        self.assertEqual(len(primary.calls), before)

    def test_rate_limit_respects_retry_after(self):
        clock = FakeClock()
        primary = FakeProvider("primary", script=[429, "back"], retry_after=5)
        backup = FakeProvider("backup")
        chain = FallbackChain([Route(primary, "m1", pool(KEY_A, clock=clock)), Route(backup, "m2")])
        self.assertEqual(chain.call("hi").route, "backup:m2")
        self.assertEqual(chain.call("hi").route, "backup:m2")  # still cooling
        clock.advance(5)
        self.assertEqual(chain.call("hi").route, "primary:m1")

    def test_outage_trips_breaker_and_skips_provider(self):
        clock = FakeClock()
        primary = FakeProvider("primary", default=503)
        backup = FakeProvider("backup")
        chain = FallbackChain(
            [
                Route(primary, "m1", pool(KEY_A), CircuitBreaker(failure_threshold=2, recovery_timeout=60, clock=clock)),
                Route(backup, "m2"),
            ]
        )
        for _ in range(2):
            chain.call("hi")
        self.assertEqual(len(primary.calls), 2)
        r = chain.call("hi")
        self.assertEqual(r.attempts[0].outcome, "circuit_open")
        self.assertEqual(len(primary.calls), 2)  # not called while open
        # Outage does not burn the key: it is still usable once the provider recovers.
        self.assertNotEqual(chain.routes[0].pool.credentials[0].state, "exhausted")
        clock.advance(60)
        primary.default = "recovered"
        self.assertEqual(chain.call("hi").route, "primary:m1")

    def test_timeout_and_broken_pipe_fall_back(self):
        primary = FakeProvider("primary", script=[TimeoutError("read timed out"), BrokenPipeError(32, "Broken pipe")])
        backup = FakeProvider("backup")
        chain = FallbackChain([Route(primary, "m1"), Route(backup, "m2")])
        self.assertEqual(chain.call("hi").attempts[0].outcome, "timeout")
        self.assertEqual(chain.call("hi").attempts[0].outcome, "connection")

    def test_bad_request_does_not_fall_back(self):
        primary = FakeProvider("primary", default=400)
        backup = FakeProvider("backup")
        chain = FallbackChain([Route(primary, "m1"), Route(backup, "m2")])
        with self.assertRaises(NonRetryableError):
            chain.call("hi")
        self.assertEqual(backup.calls, [])

    def test_everything_down_raises_with_full_trail(self):
        chain = FallbackChain(
            [Route(FakeProvider("a", default=401), "m", pool(KEY_A)), Route(FakeProvider("b", default=529), "m")]
        )
        with self.assertRaises(AllRoutesFailed) as ctx:
            chain.call("hi")
        self.assertEqual([a.outcome for a in ctx.exception.attempts], ["auth", "no_credentials", "overloaded"])

    def test_preflight_reports_each_route(self):
        chain = FallbackChain(
            [Route(FakeProvider("a", default=401), "m", pool(KEY_A)), Route(FakeProvider("b"), "m")]
        )
        report = chain.preflight("ping")
        self.assertEqual([r["healthy"] for r in report], [False, True])

    def test_on_attempt_hook_sees_every_attempt(self):
        seen = []
        chain = FallbackChain(
            [Route(FakeProvider("a", default=503), "m"), Route(FakeProvider("b"), "m")], on_attempt=seen.append
        )
        chain.call("hi")
        self.assertEqual([a.outcome for a in seen], ["unavailable", "ok"])

    def test_provider_error_kind_override(self):
        err = ProviderError("custom", status=400, kind=ErrorKind.OVERLOADED)
        self.assertEqual(err.kind, ErrorKind.OVERLOADED)


class WorkerProtocolTest(unittest.TestCase):
    def test_provider_failure_blocks_instead_of_silent_exit(self):
        chain = FallbackChain([Route(FakeProvider("a", default=401), "m", pool(KEY_A))])
        done, blocked = [], []
        ok = run_task(lambda: chain.call("task"), complete=done.append, block=blocked.append)
        self.assertFalse(ok)
        self.assertEqual(done, [])
        self.assertEqual(len(blocked), 1)
        self.assertIn("all providers failed", blocked[0])

    def test_success_completes(self):
        chain = FallbackChain([Route(FakeProvider("a"), "m")])
        done, blocked = [], []
        self.assertTrue(run_task(lambda: chain.call("task").response, complete=done.append, block=blocked.append))
        self.assertEqual(len(done), 1)
        self.assertEqual(blocked, [])


if __name__ == "__main__":
    unittest.main()
