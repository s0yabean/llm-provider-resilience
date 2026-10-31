# Runbook: scheduled jobs that fail because the model was unreachable

## Symptom

A scheduled job produced no output. Its log or request dump shows something like:

```json
{
  "reason": "max_retries_exhausted",
  "request": { "method": "POST", "url": "https://api.<provider>/chat/completions" },
  "error": { "type": "ReadError", "message": "[Errno 32] Broken pipe" }
}
```

| Field | Meaning |
|---|---|
| `max_retries_exhausted` | The framework retried the model call and every try failed |
| `ReadError` / broken pipe | Transport failure: the provider dropped the connection |
| `url` | Which provider failed |

## What did not happen

If the job was a prompt ("run the sync and report the result"), the model had
to read that prompt before anything ran. It never did. The command was never
executed, so there is nothing to retry at the tool level.

## Fixes, most reliable first

1. **Take the model out of jobs that do not need one.** Syncs, backups, health
   checks and data pulls can be plain scripts. Most agent schedulers have a
   "script only" or "no agent" mode where the script's output is delivered as
   the message. Zero model calls, zero provider risk.

   Use a model only when the job needs judgement: summarising, deciding,
   rewriting.

2. **Give the scheduler's process a fallback chain.** Check it actually
   inherits the chain; some schedulers run with a different config or profile
   from the interactive agent.

3. **Give tools a direct path.** A tool that normally routes through a gateway
   process (search, scraping) may have no gateway in the scheduled context. Set
   a direct API key for that tool so the job does not depend on the gateway.

## Diagnose a job that failed silently

1. Find the job's runs in the error log by its session or run ID.
2. Filter for `401`, `auth`, `credential`, `rate`, `timeout`.
3. Check the output folder for partial results.
4. Check credential health (`02-expired-credentials.md`).
5. Re-run the job once by hand after fixing, so it resolves credentials fresh.
