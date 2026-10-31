# Runbook: set up and test provider failover

Use this when an agent or service depends on one LLM provider and you want it
to keep working when that provider has a bad hour.

## What should trigger a switch

| Signal | Typical cause | Action |
|---|---|---|
| 401 / 403 | Key expired, revoked, or an OAuth token aged out | Next key on the same provider; if none, next provider |
| 429 | This key is rate-limited | Cool the key down (honour `Retry-After`), try the next key, then next provider |
| 529 | Provider is overloaded | Next provider |
| 500 / 502 / 503 / 504 | Provider-side fault | Next provider; count toward the circuit breaker |
| Timeout | Call hung past the client timeout | Next provider; count toward the breaker |
| Connection reset / broken pipe | Network or provider dropped the connection | Next provider; count toward the breaker |
| 400 / 422 | The request is malformed or too long | Do not fall back. Fix the request. |

**Tool failures are not provider failures.** A scraper returning an error, a
file that is missing, or a disconnected tool server should be handled by the
agent. Switching models will not fix them and hides the real problem.

## Order the chain on purpose

- Put the model you trust most first, then a different provider serving a
  comparable model, then a cheaper or local model as the last resort.
- At least one fallback should not share the primary's failure domain:
  a different company, a different credential type (API key rather than an OAuth
  token), and ideally a different network path.
- Two routes to the same model through different providers (direct, and via a
  gateway or reseller) count as separate entries and are a cheap way to add depth.

## Configuration trap: lists written as strings

Many agent CLIs have a `config set key value` command that stores everything
as a string. A fallback list written that way looks fine in the file but is
read as one string, and the framework silently reports "no fallback providers".

Write list and map values with a YAML library instead:

```python
import yaml
from pathlib import Path

path = Path("config.yaml")
config = yaml.safe_load(path.read_text())
config["fallback_providers"] = [
    {"provider": "provider-b", "model": "model-b"},
    {"provider": "provider-c", "model": "model-c"},
]
path.write_text(yaml.safe_dump(config, sort_keys=False))
```

Then confirm with the framework's own "list fallbacks" command, not by reading
the file. Most long-running processes need a restart to pick up the change.

## Test it before you need it

1. Pick a quiet time. Note the current primary key's label (not its value).
2. In a test environment, set the primary key to an invalid value.
3. Send one request. Expect a 401 on the primary and a success on the first fallback.
4. Check the logs show the switch and which route answered.
5. Restore the key, restart the process, and send one more request to confirm
   the primary answers again.

If step 3 hangs instead of failing over, see `04-unattended-workers.md`: the
fallback is probably defined in a place the process cannot reach before its
first model call succeeds.
