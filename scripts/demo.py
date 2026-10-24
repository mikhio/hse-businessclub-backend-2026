"""Exercise the running API with explicitly fictional data; no Telegram network calls."""

import argparse
import json
import uuid

import httpx

from app.auth import actor_headers
from app.config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18082")
    args = parser.parse_args()
    settings = Settings()
    if not settings.organizers:
        raise SystemExit("Set at least one ORGANIZER_IDS in .env for the demo")
    organizer = min(settings.organizers)
    guest_id = 9_000_000_000 + uuid.uuid4().int % 1_000_000_000
    with httpx.Client(base_url=args.url, timeout=20) as client:
        guest_headers = actor_headers(settings.api_key, guest_id)
        organizer_headers = actor_headers(settings.api_key, organizer)
        response = client.post(
            "/v1/guests/register",
            headers=guest_headers,
            json={"telegram_id": guest_id, "name": "ДЕМО · вымышленный гость"},
        )
        response.raise_for_status()
        qr = client.get(f"/v1/guests/{guest_id}/qr", headers=guest_headers)
        qr.raise_for_status()
        scan = client.post(
            "/v1/organizer/scan-photo",
            headers=organizer_headers,
            data={"organizer_id": organizer},
            files={"image": ("demo-pass.png", qr.content, "image/png")},
        )
        scan.raise_for_status()
        card = scan.json()
        path = f"/v1/organizer/guests/{card['id']}/check-in"
        results = []
        for _ in range(2):
            response = client.post(
                path, headers=organizer_headers, json={"organizer_id": organizer}
            )
            response.raise_for_status()
            results.append(response.json())
        assert results[0]["awarded"] is True
        assert results[1]["awarded"] is False
        assert len(results[1]["guest"]["history"]) == 1
        print(
            json.dumps(
                {
                    "data": "DEMO / fictional guest; not sent to Telegram",
                    "photo_decoded": True,
                    "first_awarded": results[0]["awarded"],
                    "second_awarded": results[1]["awarded"],
                    "points": results[1]["guest"]["points"],
                    "ledger_entries": len(results[1]["guest"]["history"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
