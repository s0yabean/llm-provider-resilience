---
name: llm-provider-resilience
description: Keep an agent working when its LLM provider fails. Use when a model call returns 401/403, 429, 529, 5xx, times out or drops the connection; when a scheduled job or background worker produced no output; when a chat bot is connected but silent; or when setting up fallback providers and key pools for unattended work.
---

# LLM provider resilience

## First, classify the failure

| Seen | Meaning | Do |
|---|---|---|
| 401 / 403 | Credential dead (expired OAuth token, revoked key) | Next key, then next provider. Fix the key: `runbooks/02-expired-credentials.md` |
| 429 | This key is throttled | Wait `Retry-After` on that key; use another key or provider meanwhile |
| 529, 5xx, timeout, broken pipe | Provider unhealthy | Next provider. Repeated failures: stop calling it for a while (circuit breaker) |
| 400 / 422 | Request is wrong | Do not fall back. Fix the request |
| Tool error (scraper, file, tool server) | Not a provider problem | Handle in the agent. Never switch models for it |

## Rules

1. Fallback lives in code or config, never only in a prompt. A worker whose
   first model call fails never reads its own instructions.
2. Probe every route before dispatching unattended work. Do not dispatch onto
   a dead primary.
3. Every unattended worker ends with exactly one of complete or block. A
   provider failure is a block with a reason, never a silent clean exit.
4. At least one fallback uses a permanent API key from a different provider
   than the primary.
5. Jobs that need no judgement (syncs, health checks, data pulls) run as plain
   scripts with no model call.
6. Never print, slice or paste a secret. Reference it by variable name; check
   it exists with `${#VAR}`.
7. After changing keys or fallback config, restart long-running processes and
   re-run the failed job once by hand.

## Which runbook

| Situation | Runbook |
|---|---|
| Setting up or testing fallback | `runbooks/01-provider-failover.md` |
| 401s, "works in chat, fails in cron" | `runbooks/02-expired-credentials.md` |
| Scheduled job produced nothing | `runbooks/03-scheduled-jobs.md` |
| Tasks stuck in `running` | `runbooks/04-unattended-workers.md` |
| Bot connected but silent | `runbooks/05-messaging-gateway-silent.md` |
| Gateway gone after reboot | `runbooks/06-gateway-persistence.md` |
| Keys mangled, `***` in files, tool cannot see key | `runbooks/07-secrets-in-agent-shells.md` |

## Code

`src/llm_resilience/` implements the table above: `FallbackChain`,
`CredentialPool`, `CircuitBreaker`, `run_task` (exit protocol) and
`FallbackChain.preflight()` (dispatch-time probe).
