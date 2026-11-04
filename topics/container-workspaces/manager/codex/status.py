"""Read workspace grants and authorized Codex account quotas."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time


def account_state(home, grants):
    accounts = sorted(p.stem for p in grants.glob("*.json") if p.is_file())
    selected = accounts[0] if accounts else None
    link = home / "auth.json"
    if link.is_symlink():
        alias = link.readlink().parent.name
        if alias in accounts:
            selected = alias
    return selected, accounts


def read_limits(account):
    # Use an isolated client: no account switch, daemon restart, or inference.
    with tempfile.TemporaryDirectory(prefix="codex-monitor-") as directory:
        Path(directory, "auth.json").symlink_to(Path("/codex-accounts") / f"{account}.json")
        env = dict(os.environ, CODEX_HOME=directory,
                   CODEX_REFRESH_TOKEN_URL_OVERRIDE="http://127.0.0.1:9/admin-managed-auth",
                   CODEX_REVOKE_TOKEN_URL_OVERRIDE="http://127.0.0.1:9/admin-managed-auth")
        process = subprocess.Popen(
            ["/opt/codex/current/bin/codex", "app-server", "--listen", "stdio://",
             "-c", 'cli_auth_credentials_store="file"'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, cwd=directory,
        )
        buffer = b""
        deadline = time.monotonic() + 20

        def send(message):
            process.stdin.write((json.dumps(message) + "\n").encode())
            process.stdin.flush()

        with selectors.DefaultSelector() as reader:
            reader.register(process.stdout, selectors.EVENT_READ)

            def response(number):
                nonlocal buffer
                while time.monotonic() < deadline:
                    if b"\n" not in buffer:
                        if not reader.select(max(0, deadline - time.monotonic())):
                            break
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            break
                        buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        message = json.loads(line)
                        if message.get("id") == number:
                            if "error" in message:
                                raise RuntimeError("Codex quota unavailable")
                            return message["result"]
                raise TimeoutError("Codex quota timed out")

            try:
                send({"id": 1, "method": "initialize", "params": {
                    "clientInfo": {"name": "workspace-monitor", "version": "1.0"}}})
                response(1)
                send({"method": "initialized"})
                send({"id": 2, "method": "account/rateLimits/read"})
                return response(2)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def quota_text(result):
    limits = (result.get("rateLimitsByLimitId") or {}).get("codex") or result.get("rateLimits") or {}
    windows = []
    for name in ("primary", "secondary"):
        window = limits.get(name)
        if not window or window.get("usedPercent") is None:
            continue
        windows.append((window.get("resetsAt"), max(0, 100 - window["usedPercent"])))
    credits = limits.get("credits") or {}
    balance = "unlimited" if credits.get("unlimited") else (
        f"{float(credits['balance']):,.0f}" if credits.get("balance") is not None else "—")
    resets = result.get("rateLimitResetCredits") or {}
    reset_count = resets.get("availableCount")
    if reset_count is not None:
        now = time.time()
        expiries = [credit["expiresAt"] for credit in resets.get("credits") or []
                    if credit.get("status") == "available" and (credit.get("expiresAt") or 0) > now]
        detail = str(reset_count)
        if reset_count and expiries:
            detail += f", {(min(expiries) - now) / 3600:.1f}h"
        balance += f" ({detail})"
    return windows, balance


def main():
    selected, accounts = account_state(Path("/codex-home"), Path("/codex-accounts"))
    pending = [account for account in accounts if account not in sys.argv[1:]]

    def query(account):
        try:
            return account, quota_text(read_limits(account))
        except (OSError, RuntimeError, ValueError, KeyError):
            return account, ("N/A", "N/A")

    with ThreadPoolExecutor(max_workers=4) as pool:
        quotas = dict(pool.map(query, pending))
    print(json.dumps({"selected": selected, "accounts": accounts, "quotas": quotas}))


if __name__ == "__main__":
    main()
