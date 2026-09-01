from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from kakao_mma_news.config import load_config
from kakao_mma_news.delivery_control import delivery_status, is_kakao_delivery_paused
from kakao_mma_news.kakao import split_message
from kakao_mma_news.kakao import post_to_kakao


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Post a generated summary Markdown file via kakaotalk-mcp.")
    parser.add_argument("--room", required=True, help="KakaoTalk chat room title")
    parser.add_argument("--summary", required=True, help="Path to summary Markdown file")
    parser.add_argument("--mcp-command", default="", help="kakaotalk-mcp executable path")
    parser.add_argument("--max-chars", type=int, default=3000, help="Maximum characters per KakaoTalk message")
    parser.add_argument("--open-attempts", type=int, default=4, help="KakaoTalk room open retry attempts")
    parser.add_argument("--open-retry-wait", type=float, default=1.5, help="Seconds to wait between room open retries")
    parser.add_argument("--tool-timeout", type=float, default=45.0, help="Maximum seconds for one MCP tool call")
    parser.add_argument("--skip-login-guard", action="store_true", help="Skip local KakaoTalk login recovery guard")
    parser.add_argument("--verify", action="store_true", help="Read recent messages and verify the first chunk")
    return parser.parse_args()


def resolve_mcp_command(value: str) -> str:
    if value:
        return value
    found = shutil.which("kakaotalk-mcp") or shutil.which("kakaotalk-mcp.exe")
    if not found:
        raise RuntimeError("kakaotalk-mcp executable was not found on PATH.")
    return found


def parse_tool_json(result: Any) -> dict[str, Any]:
    text = "\n".join(getattr(item, "text", repr(item)) for item in result.content)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


async def call_tool(
    session: Any,
    name: str,
    arguments: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        result = await asyncio.wait_for(
            session.call_tool(name, arguments),
            timeout=max(1.0, timeout_seconds),
        )
    except asyncio.TimeoutError:
        return {
            "error": f"MCP tool '{name}' timed out after {timeout_seconds:.1f} seconds",
            "timed_out": True,
        }
    return parse_tool_json(result)


async def open_room_with_retry(
    session: Any,
    room: str,
    attempts: int,
    wait_seconds: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    last_result: dict[str, Any] = {}
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        open_result = await call_tool(
            session,
            "kakao_open_room",
            {"room_name": room},
            timeout_seconds,
        )
        last_result = open_result
        if not open_result.get("error"):
            return open_result
        if open_result.get("timed_out"):
            return open_result
        open_rooms = await call_tool(session, "kakao_list_open_rooms", {}, timeout_seconds)
        print(
            json.dumps(
                {
                    "open_attempt": attempt,
                    "open_result": open_result,
                    "open_rooms": open_rooms,
                },
                ensure_ascii=False,
            )
        )
        if attempt < attempts:
            await asyncio.sleep(wait_seconds)
    return last_result


def post_chunks_to_open_room(
    args: argparse.Namespace,
    summary_path: Path,
    chunks: list[str],
) -> int:
    from kakao_mcp import controller

    sent: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        if is_kakao_delivery_paused():
            print(
                json.dumps(
                    {
                        **delivery_status(room=args.room, delivery_type="text"),
                        "summary": str(summary_path),
                        "skipped": True,
                        "chunks_sent": len(sent),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        body = chunk if len(chunks) == 1 else f"({index}/{len(chunks)})\n{chunk}"
        result = controller.send_message_to_room(args.room, body)
        sent.append(result)
        if result.get("success") is not True:
            print(json.dumps({"send_results": sent}, ensure_ascii=False))
            raise RuntimeError(f"Failed to send chunk {index}/{len(chunks)}")

    verify_found = None
    if args.verify:
        read_result = controller.read_chat_messages(args.room)
        marker = chunks[0][:80]
        verify_found = marker in (read_result.get("raw_text") or "")

    print(
        json.dumps(
            {
                "room": args.room,
                "summary": str(summary_path),
                "chunks": len(chunks),
                "sent": sent,
                "verify_found": verify_found,
                "transport": "kakaotalk-mcp-controller",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


async def post_summary(args: argparse.Namespace) -> int:
    summary_path = Path(args.summary)
    if not summary_path.exists():
        raise FileNotFoundError(f"Summary file not found: {summary_path}")
    if is_kakao_delivery_paused():
        print(
            json.dumps(
                {
                    **delivery_status(room=args.room, delivery_type="text"),
                    "summary": str(summary_path),
                    "skipped": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    message = summary_path.read_text(encoding="utf-8").lstrip("\ufeff").strip()
    if not message:
        raise RuntimeError(f"Summary file is empty: {summary_path}")

    chunks = split_message(message, args.max_chars)
    guard_result: dict[str, Any] = {}
    if not args.skip_login_guard:
        from kakao_login_guard import ensure_kakao_ready

        print(json.dumps({"phase": "kakao_login_guard_start"}), flush=True)
        guard_result = ensure_kakao_ready(
            room=args.room,
            wait_seconds=max(15.0, args.open_retry_wait * max(1, args.open_attempts)),
            open_room=False,
        )
        print(json.dumps({"kakao_login_guard": guard_result}, ensure_ascii=False), flush=True)

    existing_room_result = guard_result.get("room_result") or {}
    room_is_open = existing_room_result.get("success") is True

    if room_is_open:
        print(json.dumps({"phase": "direct_open_room_send"}), flush=True)
        return post_chunks_to_open_room(args, summary_path, chunks)

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command = resolve_mcp_command(args.mcp_command)
    params = StdioServerParameters(command=command, args=[])

    print(json.dumps({"phase": "mcp_session_start", "room_is_open": room_is_open}), flush=True)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            health = await call_tool(session, "kakao_health_check", {}, args.tool_timeout)
            if not health.get("running"):
                print(json.dumps({"health": health}, ensure_ascii=False))
                raise RuntimeError("KakaoTalk is not running on this Windows runner.")

            if room_is_open:
                open_result = existing_room_result
            else:
                open_result = await open_room_with_retry(
                    session,
                    args.room,
                    attempts=args.open_attempts,
                    wait_seconds=args.open_retry_wait,
                    timeout_seconds=args.tool_timeout,
                )
            if is_kakao_delivery_paused():
                print(
                    json.dumps(
                        {
                            **delivery_status(room=args.room, delivery_type="text"),
                            "summary": str(summary_path),
                            "skipped": True,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            if open_result.get("error"):
                print(json.dumps({"open_result": open_result}, ensure_ascii=False))
                config = load_config(None)
                config = replace(
                    config,
                    target_chatroom=args.room,
                    kakao_enabled=True,
                    kakao_send_enter=True,
                    kakao_max_chunk_chars=args.max_chars,
                    kakao_wait_seconds=max(5.0, args.open_retry_wait * 3),
                    kakao_step_delay_seconds=1.0,
                )
                posted = post_to_kakao(config, message)
                print(
                    json.dumps(
                        {
                            "room": args.room,
                            "summary": str(summary_path),
                            "chunks": len(chunks),
                            "sent": [{"message": "Message sent via clipboard fallback"}] if posted else [],
                            "verify_found": None,
                            "fallback": "clipboard",
                            "delivery_paused": not posted,
                            "skipped": not posted,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0

            sent = []
            for index, chunk in enumerate(chunks, start=1):
                if is_kakao_delivery_paused():
                    print(
                        json.dumps(
                            {
                                **delivery_status(room=args.room, delivery_type="text"),
                                "summary": str(summary_path),
                                "skipped": True,
                                "chunks_sent": len(sent),
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    return 0
                body = chunk if len(chunks) == 1 else f"({index}/{len(chunks)})\n{chunk}"
                result = await call_tool(
                    session,
                    "kakao_send_message",
                    {"room_name": args.room, "message": body},
                    args.tool_timeout,
                )
                sent.append(result)
                if result.get("error"):
                    print(json.dumps({"send_results": sent}, ensure_ascii=False))
                    raise RuntimeError(f"Failed to send chunk {index}/{len(chunks)}")

            verify_found = None
            if args.verify:
                read_result = await call_tool(
                    session,
                    "kakao_read_messages",
                    {"room_name": args.room, "max_messages": 5},
                    args.tool_timeout,
                )
                recent = read_result.get("messages") or []
                marker = chunks[0][:80]
                verify_found = any(
                    marker in (message.get("text") or "")
                    for message in recent
                    if isinstance(message, dict)
                )

            print(
                json.dumps(
                    {
                        "room": args.room,
                        "summary": str(summary_path),
                        "chunks": len(chunks),
                        "sent": sent,
                        "verify_found": verify_found,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    return asyncio.run(post_summary(args))


if __name__ == "__main__":
    raise SystemExit(main())
