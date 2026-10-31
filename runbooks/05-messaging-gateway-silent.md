# Runbook: chat bot is connected but does not answer

For an agent exposed through a messaging gateway (Telegram, Discord, Slack and
similar). The gateway process can be healthy while messages go nowhere. The
fault is almost always in one segment of the round trip, so test segments one
at a time instead of guessing.

## The four segments

1. **Right bot.** Is the user messaging the bot this gateway controls?
2. **Inbound.** Does the user's message reach the gateway log?
3. **Agent.** Does the gateway process it without an error (including a provider error)?
4. **Outbound.** Can the gateway send a message to that chat?

The single most useful probe: **send a message out to the user from the
gateway.** If they see it, outbound and right-chat are proven and the problem
is inbound or an allowlist. If they do not, they are looking at another bot or chat.

## Steps

1. **Gateway alive?** Check its status, that exactly one gateway process is
   running, and that the log shows a successful connect. This is rarely the
   cause, but rule it out.
2. **Bot identity.** Ask the platform which bot the configured token belongs
   to (Telegram: `getMe`) and have the user confirm the bot's username matches
   the one they are chatting with. Mismatched bots are the most common cause.
3. **Allowlist.** If the gateway restricts which user IDs it answers, messages
   from anyone else are dropped silently: no log line, no reply. Confirm the
   user's ID is on the list.
4. **Watch the log while the user sends a test message.** Take the line count,
   wait, print only the new lines:
   ```bash
   L=gateway.log; b=$(wc -l < "$L"); echo "send a message now"; sleep 25; tail -n +$((b+1)) "$L"
   ```
   Nothing new: the message never arrived (wrong bot, or the user never started the chat).
   A line but no reply: look for an exception in the same window. A 401 or 503
   here means the provider, not the gateway, is the problem (`01-provider-failover.md`).
5. **Outbound probe** to the user's chat, as above.

## Signals that mislead

- **`409 Conflict` from a manual `getUpdates` is normal** while the gateway is
  polling. It means the gateway holds the long-poll. It does not mean two
  gateways are running. It also knocks the gateway into a short retry, so
  avoid manual `getUpdates`; use `getMe` and `getWebhookInfo` instead.
- **"Chat not found" on outbound** means the user has never started a chat with
  this bot. A bot cannot message a user first. This is decisive evidence of the
  wrong-bot case.
- **A bare `/start` press may be ignored by design.** Ask for real text.
- **No log line** cannot tell "wrong bot" from "allowlisted out". Use the outbound probe.
- **`getWebhookInfo`**: an empty URL means polling mode. A webhook URL set
  while a poller runs is a conflict. `pending_update_count: 0` with the user
  insisting they sent messages means the messages were consumed and dropped or
  went elsewhere.

Keep the bot token inside the shell for all of these probes; see
`07-secrets-in-agent-shells.md`.
