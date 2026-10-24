"""Create a private local .env with independent random development secrets."""

import argparse
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--organizers", default="", help="Comma-separated numeric Telegram IDs")
    args = parser.parse_args()
    values = args.organizers.split(",")
    if any(value.strip() and (not value.strip().isdigit() or int(value) <= 0) for value in values):
        parser.error("Organizer IDs must be positive integers")
    path = Path(".env")
    with path.open("x") as file:
        path.chmod(0o600)
        file.write(
            f"POSTGRES_PASSWORD={secrets.token_urlsafe(32)}\n"
            f"API_KEY={secrets.token_urlsafe(32)}\n"
            f"PASS_SIGNING_KEY={secrets.token_urlsafe(32)}\n"
            "BOT_TOKEN=\n"
            f"ORGANIZER_IDS={args.organizers}\n"
            "EVENT_ID=hse-businessclub-demo-2026\nCHECKIN_POINTS=10\nAPI_PORT=18082\n"
        )
    print("Created .env (0600). Set BOT_TOKEN there before starting the telegram profile.")


if __name__ == "__main__":
    main()
