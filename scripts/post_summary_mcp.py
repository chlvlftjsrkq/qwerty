from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kakao_mma_news.kakao import split_message


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Post a generated summary Markdown file via kakaotalk-mcp.")
    parser.add_argument("--room", required=True, help="KakaoTalk chat room title")
    parser.add_argument("--summary", required=True, help="Path to summary Markdown file")
    parser.add_argument("--mcp-command", default="", help="kakaotalk-mcp executable path")
    parser.add_argument("--max-chars", type=int, default=3000, help="Maximum characters per KakaoTalk message")
    parser.add_argument("--open-attempts", type=int, default=4, help="KakaoTalk room open retry attempts")
    parser.add_argument("--open-retry-wait", type=float, default=1.5, help="Seconds to wait between room open retries")
    parser.add_argument("--allow-mouse-send-fallback", action="store_true", help="Allow the older mouse-click based MCP sender if no-mouse sending fails")
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


def send_message_without_mouse(room: str, message: str) -> dict[str, Any]:
    """Paste and send text into the Kakao edit control without clicking the chat body."""
    try:
        import win32clipboard
        import win32con
        import win32gui
        from kakao_mcp import controller
    except Exception as exc:
        return {"success": False, "error": f"safe_send_unavailable: {exc}"}

    hwnd = controller.find_chat_window(room)
    if hwnd is None:
        return {"success": False, "error": f"Chat window '{room}' not found"}
    edit_hwnd = controller.find_child_window_recursive(hwnd, controller.config.KAKAO_EDIT_CLASS)
    if edit_hwnd is None:
        return {"success": False, "error": f"Edit control not found in '{room}'"}

    controller.bring_window_to_front(hwnd)
    time.sleep(0.25)

    user32 = ctypes.windll.user32
    try:
        win32gui.SetFocus(edit_hwnd)
    except Exception:
        user32.SetFocus(int(edit_hwnd))
    time.sleep(0.1)

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(message, win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()

    user32.keybd_event(0x11, 0, 0, 0)  # Ctrl down
    user32.keybd_event(0x56, 0, 0, 0)  # V down
    user32.keybd_event(0x56, 0, 0x0002, 0)  # V up
    user32.keybd_event(0x11, 0, 0x0002, 0)  # Ctrl up
    time.sleep(0.25)

    user32.keybd_event(0x0D, 0, 0, 0)
    user32.keybd_event(0x0D, 0, 0x0002, 0)
    return {"success": True, "message": f"Message sent to '{room}' with no mouse click"}


async def call_tool(session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(name, arguments)
    return parse_tool_json(result)


async def open_room_with_retry(session: ClientSession, room: str, attempts: int, wait_seconds: float) -> dict[str, Any]:
    last_result: dict[str, Any] = {}
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        open_result = await call_tool(session, "kakao_open_room", {"room_name": room})
        last_result = open_result
        if not open_result.get("error"):
            return open_result
        open_rooms = await call_tool(session, "kakao_list_open_rooms", {})
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


async def post_summary(args: argparse.Namespace) -> int:
    summary_path = Path(args.summary)
    if not summary_path.exists():
        raise FileNotFoundError(f"Summary file not found: {summary_path}")
    message = summary_path.read_text(encoding="utf-8").lstrip("\ufeff").strip()
    if not message:
        raise RuntimeError(f"Summary file is empty: {summary_path}")

    chunks = split_message(message, args.max_chars)
    command = resolve_mcp_command(args.mcp_command)
    params = StdioServerParameters(command=command, args=[])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            health = await call_tool(session, "kakao_health_check", {})
            if not health.get("running"):
                print(json.dumps({"health": health}, ensure_ascii=False))
                raise RuntimeError("KakaoTalk is not running on this Windows runner.")

            open_result = await open_room_with_retry(
                session,
                args.room,
                attempts=args.open_attempts,
                wait_seconds=args.open_retry_wait,
            )
            if open_result.get("error"):
                print(json.dumps({"open_result": open_result}, ensure_ascii=False))
                raise RuntimeError(f"Failed to open KakaoTalk room: {args.room}")

            sent = []
            for index, chunk in enumerate(chunks, start=1):
                body = chunk if len(chunks) == 1 else f"({index}/{len(chunks)})\n{chunk}"
                result = send_message_without_mouse(args.room, body)
                if result.get("error") and args.allow_mouse_send_fallback:
                    result = await call_tool(
                        session,
                        "kakao_send_message",
                        {"room_name": args.room, "message": body},
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
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    return asyncio.run(post_summary(args))


if __name__ == "__main__":
    raise SystemExit(main())
