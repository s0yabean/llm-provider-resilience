"""Sketch: wrap an OpenAI-compatible chat endpoint as a Provider (stdlib only).

Most hosted and local model servers expose this request shape, so one adapter
covers several routes. Not used by the tests. To try it for real, set the
variables named in .env.example and run:

    python examples/http_adapter.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm_resilience import CredentialPool, FallbackChain, ProviderError, Route  # noqa: E402


class OpenAICompatibleProvider:
    def __init__(self, name: str, base_url: str, timeout: float = 30.0) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout  # always set one: a hung call is worse than a failed one

    def complete(self, model, request, secret):
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": request}]}).encode()
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            retry_after = e.headers.get("Retry-After")
            raise ProviderError(
                f"{self.name} HTTP {e.code}",
                status=e.code,
                retry_after=float(retry_after) if retry_after and retry_after.isdigit() else None,
            ) from None
        # URLError, TimeoutError and socket errors propagate; the chain classifies them.
        return data["choices"][0]["message"]["content"]


if __name__ == "__main__":
    routes = []
    if os.environ.get("PRIMARY_BASE_URL"):
        routes.append(
            Route(
                OpenAICompatibleProvider("primary", os.environ["PRIMARY_BASE_URL"]),
                os.environ.get("PRIMARY_MODEL", ""),
                CredentialPool.from_env("PRIMARY_API_KEY_1", "PRIMARY_API_KEY_2"),
            )
        )
    if os.environ.get("BACKUP_BASE_URL"):
        routes.append(
            Route(
                OpenAICompatibleProvider("backup", os.environ["BACKUP_BASE_URL"]),
                os.environ.get("BACKUP_MODEL", ""),
                CredentialPool.from_env("BACKUP_API_KEY_1"),
            )
        )
    if not routes:
        sys.exit("No routes configured. Copy .env.example to .env, fill it in, and export it.")
    chain = FallbackChain(routes)
    for row in chain.preflight("Reply with the word ok."):
        print(row)
