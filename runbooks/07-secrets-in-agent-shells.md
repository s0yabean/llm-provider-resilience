# Runbook: secrets get corrupted when an agent handles them

Agent frameworks scan tool input and output for anything that looks like a
key and redact it. That is the right default, but it breaks scripts that try
to move or inspect a secret in text the agent can see, and the errors look
like your code is wrong.

## Symptoms

- A key copied by the agent into a command gives 401 or "invalid API key",
  and the error shows the key partly replaced by `***`.
- A file written by the agent has a `SyntaxError` on a line that looks fine:
  the redactor cut the line at a `***` inside a string literal.
- `env | grep KEY` shows `KEY=***` even though the variable is not set. Some
  redactors insert decoy lines so the output cannot be used to enumerate keys.

## Rules

1. **Keep secrets inside the shell.** Load them into environment variables and
   reference them by name. The value never appears in a tool call or its output.
   ```bash
   set -a; . ./.env; set +a
   curl -s -H "Authorization: Bearer ${PROVIDER_API_KEY}" "https://api.example.com/v1/models"
   ```
2. **Never paste a key into a command string,** and never print or slice it to
   check it.
3. **Check a variable exists by length,** not by printing it:
   ```bash
   echo "${#PROVIDER_API_KEY}"   # 0 = unset or empty
   ```
4. **Parse secret-bearing files from a script file,** not an inline
   `python -c` one-liner. Inline code is more likely to be mangled by redaction
   and quoting, and piping a response into an interpreter is often flagged by
   security scanners.
5. **Avoid writing the literal `***` in generated files.** If a script must
   contain it, build it at runtime (`"*" * 3`).

## Passing keys to other tools

External tools and tool servers (MCP servers, CLIs) do not inherit the agent's
credential store. Each needs the key passed explicitly:

- Check the variable name the tool expects against what your `.env` actually
  sets. A tool may want `PROVIDER_API_KEY` while your file has the real value
  under `PROVIDER_TOKEN` and an empty `PROVIDER_API_KEY=` line.
- For a tool server, put the key in that server's own `env` block in the
  config. A key in the main `.env` alone often does not reach the child
  process, and the server fails with "connection closed".
- Write list and map config values with a YAML library, not a string-only
  `config set` command (see `01-provider-failover.md`).
- Test the tool on its own (`--help` or a one-off run with the key in its
  environment) before registering it.
- Config changes need a restart. Some frameworks block a restart from inside
  the agent session; the operator must run it from their own terminal.

Leave redaction on. Turning it off to make a script work trades a debugging
annoyance for a leak.
