# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Call the value-search API with a concrete example.

Start the backend first, then run:

    uv run python -m dev_tools.test_value_search_api
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test the value-search API")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:3001",
        help="GSF backend base URL",
    )
    parser.add_argument("--database-name", default="regional_sales")
    parser.add_argument("--value", default="Weimei Corp")
    parser.add_argument("--description", default="Customer name")
    args = parser.parse_args(argv)

    payload = {
        "database_name": args.database_name,
        "value": args.value,
        "description": args.description,
    }
    request = Request(
        f"{args.base_url.rstrip('/')}/api/value-search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310
            result = json.load(response)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"API returned HTTP {exc.code}: {body}", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Could not reach the GSF backend: {exc.reason}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
