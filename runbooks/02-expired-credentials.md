# Runbook: expired or exhausted credentials

The most common failure in headless and scheduled environments is not an
outage. It is a key that stopped working while nobody was watching.

## Know which kind of credential you have

| Kind | Expires? | Refresh |
|---|---|---|
| Permanent API key | No (until revoked) | Not needed |
| OAuth bearer token from a CLI login | Yes, periodically | Needs a browser login on some machine, then copy the new token across |

A server with no browser cannot refresh an OAuth token itself. If your
primary route uses one, assume it will expire and make sure the first fallback
uses a permanent key.

Check the prefix only, never print the whole value:

```bash
echo "${PROVIDER_TOKEN:0:8}"
```

## Know where each process reads keys from

Frameworks often have two stores: a `.env` file and their own credential store
(for example an `auth.json` written by an `auth add` command). They do not
always sync.

| Process | Reads keys | Consequence |
|---|---|---|
| Long-running gateway / server | Once, at startup | Keeps working on a cached key after the file changes, in both directions |
| Scheduled job (cron) | Fresh, each run | Sees the file as it is now |
| `auth add`-style command | Writes its own store only | Cron may still see the old key in `.env` |

This explains the confusing pattern "the chat bot works but the nightly job
fails": the gateway cached a key that was valid at startup, the job reads the
file and gets the expired one. A restart of the gateway would break it too.

Compare the two times:

```bash
ps -o lstart= -p "$(pgrep -f '<gateway process name>')"
stat -c '%y' .env      # GNU; on macOS use: stat -f '%Sm' .env
```

If `.env` changed after the gateway started, the gateway is on stale credentials.

## Read the pool's health

If the framework keeps per-key health, look for:

- `last_status`: untested / ok / exhausted
- `last_error_code`: 401, 429, 503
- the key's source (env variable or manually added)

An exhausted key is skipped until it is reset. Replacing the value in `.env`
alone may not clear that flag.

## Fix

1. Get a new key or token. Prefer a permanent key for unattended work.
2. Update every store the processes read from (`.env` and the framework's own store).
   Edit with a script rather than `sed` so special characters in the key do
   not break the substitution, and never echo the key.
3. Reset the exhausted flag for that provider.
4. Restart long-running processes.
5. Trigger the failed scheduled job once by hand and confirm it succeeds.

In this repo's module, `CredentialPool.replace(label, new_secret)` does steps
2 and 3 together: a new fingerprint clears the old key's failure state, the
same value leaves it exhausted.
