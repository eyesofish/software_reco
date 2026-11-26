"""Create local demo credentials without writing secrets into tracked configuration."""

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".runtime" / "auth-demo.env"


def initialize() -> Path:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    password_file = ENV_FILE.with_name("postgres-password")
    try:
        descriptor = os.open(password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(secrets.token_hex(32))
    if not ENV_FILE.exists():
        agent_key = secrets.token_hex(32)
        values = {
            "JWT_SECRET": secrets.token_hex(32),
            "AGENT_API_KEY": agent_key,
            "API_KEY": agent_key,
            "DEMO_ALICE_PASSWORD": secrets.token_hex(8),
            "DEMO_BOB_PASSWORD": secrets.token_hex(8),
        }
        # Exclusive creation prevents replacing credentials when two launchers race.
        try:
            with ENV_FILE.open("x", encoding="utf-8") as stream:
                stream.write("".join(f"{key}={value}\n" for key, value in values.items()))
            ENV_FILE.chmod(0o600)
        except FileExistsError:
            pass
    return ENV_FILE


if __name__ == "__main__":
    print(f"Demo accounts: alice / bob. Credentials: {initialize()}")
