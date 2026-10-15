"""One-time setup: register your Twilio Elastic SIP trunk with LiveKit as an
outbound trunk, and print the trunk id to put in .env (SIP_OUTBOUND_TRUNK_ID).

    python scripts/setup_trunk.py            # create
    python scripts/setup_trunk.py --list     # show existing outbound trunks
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livekit import api  # noqa: E402

from caller import config  # noqa: E402,F401  (loads .env)


def need(name: str) -> str:
    value = os.getenv(name)
    if not value:
        sys.exit(f"Missing {name} in .env (see .env.example)")
    return value


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="list outbound trunks and exit")
    args = parser.parse_args()

    async with api.LiveKitAPI() as lk:
        if args.list:
            res = await lk.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest())
            for t in res.items:
                print(f"{t.sip_trunk_id}  {t.name}  {t.address}  numbers={list(t.numbers)}")
            return

        trunk = api.SIPOutboundTrunkInfo(
            name="patient-caller",
            address=need("TWILIO_TERMINATION_URI"),  # e.g. my-trunk.pstn.twilio.com
            numbers=[need("CALLER_ID_NUMBER")],  # your one Twilio number, E.164
            auth_username=need("TWILIO_SIP_USERNAME"),
            auth_password=need("TWILIO_SIP_PASSWORD"),
        )
        created = await lk.sip.create_outbound_trunk(api.CreateSIPOutboundTrunkRequest(trunk=trunk))
        print("Created outbound trunk.")
        print(f"Add this to .env:\nSIP_OUTBOUND_TRUNK_ID={created.sip_trunk_id}")


if __name__ == "__main__":
    asyncio.run(main())
