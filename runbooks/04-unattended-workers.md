# Runbook: workers that die silently and leave tasks stuck

Applies to queue or kanban-style systems where a dispatcher hands tasks to
short-lived agent workers.

## The failure sequence

1. A shared credential expires (say, the OAuth token every worker uses).
2. The dispatcher starts several workers on the same provider.
3. Each worker's first model call returns 401.
4. The worker is interrupted mid-call and exits with code 0. It never reports
   the task as done or blocked.
5. The dispatcher sees a clean exit with no status, flags a protocol violation,
   and the task stays in `running` with a stale lock and an ageing heartbeat.
6. Nobody is watching. The missing output is noticed hours later.

One expired credential takes down every worker at once, because they share it.

## Why "fallback instructions in the task" do not work

A common fix is to write "if the provider fails, switch to the next one" in
the task description. The worker has to call the model to read that sentence.
If the very first call fails, the instruction is never read.

**Fallback has to happen in code or config at dispatch time, not in the prompt.**
Instructions in the task only help for an outage that starts mid-session.

## Prevention

1. **Probe before dispatch.** Run a one-turn health check on every route the
   workers will use. If the primary is down, either switch the workers' config
   or do not dispatch. (`FallbackChain.preflight()` in this repo.)
2. **Configure the fallback chain in the worker's profile or config,** so it
   applies to the first call.
3. **Enforce the exit protocol in code.** Every worker ends with exactly one of
   `complete(result)` or `block(reason)`. A provider failure becomes
   `block("all providers failed: ...")`, never a silent exit.
   (`run_task()` in this repo.)
4. **Cap concurrency.** Start low (two workers) and raise it only after you have
   measured the dispatcher under load. Many workers starting together can
   saturate the dispatcher, and when they all fail together recovery is slower.
5. **Do not share one credential across every worker** if a pool is available.

## Detection and recovery

- List tasks in `running` for more than twice their maximum runtime: likely zombies.
- Check the error log for 401 / authentication around the task start times.
- Check worker processes are actually alive.
- If a worker wrote its output file but never reported, verify the file and
  mark the task complete by hand. Recreate downstream tasks if the chain broke.
- Otherwise kill the task, fix the credential, and re-queue.
- A worker with no output for more than a couple of minutes after start is
  worth killing and diagnosing rather than waiting on.
