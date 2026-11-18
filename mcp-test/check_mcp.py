#!/usr/bin/env python3
"""
Check whether an MCP endpoint advertises tools.

Python 3.11+
Dependencies: mcp<2, httpx

Only initializes a session and lists tools.
Never sends tools/call.
"""

import argparse
import asyncio
import getpass
import json
import logging
import os
import ssl
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


# Avoid SDK debug logs containing session data or response bodies.
logging.disable(logging.CRITICAL)


def leaf_errors(error):
    """Unwrap exceptions raised by asynchronous task groups."""
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            yield from leaf_errors(child)
    else:
        yield error


def safe_text(value, secret):
    """Redact the supplied token and escape terminal control characters."""
    text = str(value)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    return json.dumps(text, ensure_ascii=True)


async def inspect_server(args, token, state):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    tls = ssl.create_default_context(cafile=args.ca_file)

    async def observe_response(response):
        # Show status and content type, not authorization/session headers.
        content_type = response.headers.get("content-type", "")
        print(
            f"  HTTP {response.request.method} "
            f"{response.status_code} "
            f"{safe_text(content_type, token)}",
            flush=True,
        )

        state["http"].append({
            "method": response.request.method,
            "status": response.status_code,
            "content_type": content_type,
        })

        # Do not forward credentials through redirects.
        if 300 <= response.status_code < 400:
            raise RuntimeError(
                "Endpoint redirected the request. Use the exact final "
                "HTTPS production MCP URL, not a login or redirect URL."
            )

    async with httpx.AsyncClient(
        headers=headers,
        verify=tls,
        follow_redirects=False,
        timeout=httpx.Timeout(args.timeout, connect=10.0),
        event_hooks={"response": [observe_response]},
    ) as http:
        async with streamable_http_client(
            args.url,
            http_client=http,
        ) as (read, write, _):
            async with ClientSession(
                read,
                write,
                read_timeout_seconds=timedelta(seconds=args.timeout),
            ) as session:
                state["stage"] = "initialize"
                print("\nInitializing MCP session...", flush=True)
                initialized = await session.initialize()

                print(
                    "Server:",
                    safe_text(initialized.serverInfo.name, token),
                )
                print(
                    "Server version:",
                    safe_text(initialized.serverInfo.version, token),
                )
                print("Protocol:", initialized.protocolVersion)
                print(
                    "Declares tools capability:",
                    initialized.capabilities.tools is not None,
                )

                state["stage"] = "tools/list"
                print("\nRequesting advertised tools...", flush=True)

                tools = []
                cursor = None
                seen_cursors = set()

                # Follow pagination, with a bound against broken servers.
                for _ in range(100):
                    page = await session.list_tools(cursor=cursor)
                    tools.extend(page.tools)

                    cursor = page.nextCursor
                    if not cursor:
                        break

                    if cursor in seen_cursors:
                        raise RuntimeError(
                            "Server repeated a tools/list pagination cursor."
                        )
                    seen_cursors.add(cursor)
                else:
                    raise RuntimeError(
                        "Stopped after 100 pages; tool inventory is incomplete."
                    )

                report = {
                    "server": initialized.serverInfo.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ),
                    "protocol": initialized.protocolVersion,
                    "capabilities": initialized.capabilities.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ),
                    "tool_count": len(tools),
                    "tools": [
                        tool.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        )
                        for tool in tools
                    ],
                }

                print(f"\nAdvertised tools: {len(tools)}")

                for index, tool in enumerate(tools, start=1):
                    print(f"\n{index}. {safe_text(tool.name, token)}")
                    print(
                        "   Description:",
                        safe_text(tool.description or "(none)", token),
                    )
                    if args.schemas:
                        schema = json.dumps(
                            tool.inputSchema,
                            indent=2,
                            ensure_ascii=True,
                        )
                        if token:
                            schema = schema.replace(token, "[REDACTED]")
                        print("   Input schema:")
                        print(schema)

                if tools:
                    print(
                        "\nPASS: The endpoint advertised tools. "
                        "No tools were called."
                    )
                else:
                    print(
                        "\nEMPTY: MCP initialization and tools/list succeeded, "
                        "but the server returned no tools."
                    )

                # Keep the result even if session cleanup subsequently fails.
                state["report"] = report
                state["stage"] = "session cleanup"

    state["stage"] = "complete"
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Initialize an MCP server and list its advertised tools."
    )
    parser.add_argument("url", help="Exact HTTPS production MCP URL")
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Do not send a bearer token",
    )
    parser.add_argument(
        "--schemas",
        action="store_true",
        help="Print tool input schemas",
    )
    parser.add_argument(
        "--output",
        help="Save server metadata and tool definitions as JSON",
    )
    parser.add_argument(
        "--ca-file",
        help="Optional PEM CA certificate for a private CA",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30,
        help="Per-request timeout in seconds, default: 30",
    )
    args = parser.parse_args()

    parsed = urlsplit(args.url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        parser.error(
            "Use an HTTPS URL without embedded credentials, query, or fragment."
        )
    if args.timeout <= 0:
        parser.error("--timeout must be positive.")

    token = ""
    if not args.no_auth:
        token = os.environ.get("N8N_MCP_TOKEN", "")
        if not token:
            token = getpass.getpass("MCP bearer token: ")
        if not token:
            parser.error("Token is empty. Use --no-auth for an open endpoint.")
        if "\r" in token or "\n" in token:
            parser.error("Token contains a newline.")
        if token.lower().startswith("bearer "):
            parser.error("Enter only the token, without the 'Bearer ' prefix.")

    state = {"stage": "connection setup", "http": [], "report": None}
    print(f"Testing: {args.url}")
    print("Transport: Streamable HTTP")
    print("Authentication:", "Bearer" if token else "none")

    async def bounded_run():
        # Bound the overall test as well as individual requests.
        async with asyncio.timeout(args.timeout * 3 + 15):
            return await inspect_server(args, token, state)

    failed = False
    try:
        asyncio.run(bounded_run())
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except Exception as error:
        failed = True
        print(f"\nFAILED during: {state['stage']}", file=sys.stderr)
        for item in leaf_errors(error):
            print(
                f"  {type(item).__name__}: "
                f"{safe_text(str(item)[:1500], token)}",
                file=sys.stderr,
            )

    report = state["report"]
    if report is not None and args.output:
        contents = json.dumps(report, indent=2, ensure_ascii=True)
        if token:
            contents = contents.replace(token, "[REDACTED]")
        Path(args.output).write_text(contents + "\n")
        print(f"\nSaved tool inventory to {args.output}")

    if failed:
        if report is not None:
            print(
                "\nTool discovery completed, but cleanup failed. "
                "The inventory above is still useful."
            )
        else:
            print(
                "\nNo complete inventory was obtained. "
                "This does NOT establish that the workflow has zero tools."
            )
        return 1

    return 0 if report and report["tool_count"] else 2


if __name__ == "__main__":
    sys.exit(main())

