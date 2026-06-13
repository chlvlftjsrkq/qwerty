from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import sys
import time
from pathlib import Path

import pyautogui
import pyperclip
from kakao_mcp import controller
from kakao_login_guard import ensure_kakao_ready

pyautogui.FAILSAFE = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Attach an image file to a KakaoTalk room via the PC Kakao UI.")
    parser.add_argument("--room", required=True, help="KakaoTalk chat room title")
    parser.add_argument("--image", required=True, help="Image file to attach")
    parser.add_argument("--screenshot", default="", help="Optional screenshot path after sending")
    parser.add_argument("--open-wait", type=float, default=1.2)
    parser.add_argument("--open-attempts", type=int, default=4)
    parser.add_argument("--open-retry-wait", type=float, default=1.5)
    parser.add_argument("--send-wait", type=float, default=4.0)
    return parser.parse_args()


USER32 = ctypes.windll.user32


def enum_window_titles() -> list[tuple[int, str]]:
    titles: list[tuple[int, str]] = []
    buffer = ctypes.create_unicode_buffer(512)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def proc(hwnd: int, _lparam: int) -> bool:
        if USER32.IsWindowVisible(hwnd):
            USER32.GetWindowTextW(hwnd, buffer, 512)
            title = buffer.value
            if title:
                titles.append((int(hwnd), title))
        return True

    USER32.EnumWindows(proc, 0)
    return titles


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = ctypes.wintypes.RECT()
    USER32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def open_dialog_visible() -> bool:
    return any(title in {"열기", "Open"} for _, title in enum_window_titles())


def bring_room_to_front(
    room: str,
    wait: float,
    attempts: int,
    retry_wait: float,
) -> tuple[int, tuple[int, int, int, int], dict]:
    open_result = {}
    hwnd = None
    for attempt in range(1, max(1, attempts) + 1):
        open_result = controller.search_and_open_room(room)
        time.sleep(wait)
        hwnd = controller.find_chat_window(room)
        if not hwnd:
            pyautogui.hotkey("ctrl", "f")
            time.sleep(0.5)
            pyperclip.copy(room)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.5)
            pyautogui.press("enter")
            time.sleep(wait)
            hwnd = controller.find_chat_window(room)
        if hwnd:
            break
        print(
            json.dumps(
                {
                    "open_attempt": attempt,
                    "room": room,
                    "open_result": open_result,
                    "open_windows": enum_window_titles(),
                },
                ensure_ascii=False,
            )
        )
        time.sleep(retry_wait)
    if not hwnd:
        raise RuntimeError(f"KakaoTalk room window was not found: {room}")
    controller.bring_window_to_front(hwnd)
    time.sleep(wait)
    return int(hwnd), get_window_rect(hwnd), open_result


def attach_image(
    room: str,
    image_path: Path,
    screenshot_path: Path | None,
    open_wait: float,
    open_attempts: int,
    open_retry_wait: float,
    send_wait: float,
) -> dict:
    if not image_path.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    guard_result = ensure_kakao_ready(room=room, wait_seconds=max(15.0, open_wait * max(1, open_attempts)))
    print(json.dumps({"kakao_login_guard": guard_result}, ensure_ascii=False))

    hwnd, rect, open_result = bring_room_to_front(room, open_wait, open_attempts, open_retry_wait)
    left, _top, _right, bottom = rect

    # PC Kakao places the file attachment icon near the lower-left of the chat window.
    file_icon_x = left + 87
    file_icon_y = bottom - 25
    pyautogui.click(file_icon_x, file_icon_y)
    time.sleep(1.0)
    dialog_after_click = open_dialog_visible()

    if not dialog_after_click:
        # Open-chat notice or slow UI animation can intercept the first click.
        pyautogui.press("enter")
        time.sleep(0.8)
        if not open_dialog_visible():
            pyautogui.click(file_icon_x, file_icon_y)
            time.sleep(1.0)

    pyperclip.copy(str(image_path))
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(2.0)

    # The file-send confirmation panel opens inside the Kakao room. The send button
    # sits at the lower-right of that panel on the current PC Kakao layout.
    pyautogui.click(left + 565, bottom - 24)
    time.sleep(send_wait)

    controller.bring_window_to_front(hwnd)
    time.sleep(0.5)
    if screenshot_path:
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        pyautogui.screenshot().save(screenshot_path)

    return {
        "room": room,
        "image": str(image_path),
        "hwnd": hwnd,
        "rect": list(rect),
        "open_result": open_result,
        "dialog_after_click": dialog_after_click,
        "screenshot": str(screenshot_path) if screenshot_path else "",
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    result = attach_image(
        room=args.room,
        image_path=Path(args.image).resolve(),
        screenshot_path=Path(args.screenshot).resolve() if args.screenshot else None,
        open_wait=args.open_wait,
        open_attempts=args.open_attempts,
        open_retry_wait=args.open_retry_wait,
        send_wait=args.send_wait,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
