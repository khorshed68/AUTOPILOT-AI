#!/usr/bin/env python3
"""Create .env from .env.example, filling in strong random secrets. Never overwrites an existing .env."""
import pathlib
import secrets
import sys

root = pathlib.Path(__file__).resolve().parent.parent
target = root / ".env"
if target.exists():
    sys.exit(".env already exists; leaving it unchanged.")

generated = {"SECRET_KEY": secrets.token_urlsafe(48), "ENCRYPTION_KEY": secrets.token_urlsafe(48), "POSTGRES_PASSWORD": secrets.token_urlsafe(24)}
lines = []
for line in (root / ".env.example").read_text().splitlines():
    key = line.split("=", 1)[0]
    lines.append(f"{key}={generated[key]}" if key in generated else line)
target.write_text("\n".join(lines) + "\n")
print("Created .env with generated secrets.")
