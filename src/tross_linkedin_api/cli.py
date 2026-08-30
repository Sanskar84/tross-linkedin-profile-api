"""Local reverse-engineering utilities; not part of the hosted API surface."""

import argparse
import asyncio
import sys
from pathlib import Path

from curl_cffi.requests.exceptions import RequestException
from pydantic import ValidationError

from tross_linkedin_api.clients.linkedin import (
    ProfilePageTransport,
    create_linkedin_config,
    create_linkedin_session,
)
from tross_linkedin_api.config import MissingLinkedInCookiesError, settings
from tross_linkedin_api.errors import ApplicationError
from tross_linkedin_api.inspection import inspect_profile_page


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect a LinkedIn SSR profile response without printing raw HTML.",
    )
    parser.add_argument("profile_url", help="LinkedIn /in/ profile URL")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional private path for the raw HTML (use the ignored tmp/ directory).",
    )
    return parser


async def run(profile_url: str, output_path: Path | None) -> int:
    try:
        cookies = settings.linkedin_cookies()
        config = create_linkedin_config(settings)
        session = create_linkedin_session(cookies, config)
        try:
            transport = ProfilePageTransport(session)
            summary = await inspect_profile_page(
                profile_url,
                transport,
                output_path=output_path,
            )
        finally:
            await session.close()
    except (ApplicationError, MissingLinkedInCookiesError, ValidationError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except RequestException:
        print("error: LinkedIn request failed.", file=sys.stderr)
        return 1

    print(summary.model_dump_json(indent=2))
    return 0


def main() -> None:
    arguments = build_parser().parse_args()
    raise SystemExit(asyncio.run(run(arguments.profile_url, arguments.output)))


if __name__ == "__main__":
    main()
