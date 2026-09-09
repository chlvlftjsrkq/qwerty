from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pyautogui
import pyperclip
from kakao_mcp import controller
from kakao_mma_news.delivery_control import delivery_status, is_kakao_delivery_paused
from kakao_mma_news.kakao_ui_guard import (
    close_owned_common_dialogs,
    file_icon_point,
    owned_common_dialogs,
    owned_file_dialogs,
    submit_file_dialog,
    wait_for_new_file_dialog,
)
from kakao_login_guard import ensure_chat_tab, ensure_kakao_ready

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


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    import win32gui

    return tuple(int(value) for value in win32gui.GetWindowRect(hwnd))


def bring_room_to_front(
    room: str,
    wait: float,
    attempts: int,
    retry_wait: float,
) -> tuple[int, tuple[int, int, int, int], dict]:
    hwnd = controller.find_chat_window(room)
    open_result = (
        {
            "success": True,
            "message": f"Chat room '{room}' is already open",
            "hwnd": int(hwnd),
            "already_open": True,
        }
        if hwnd
        else {}
    )
    for attempt in range(1, max(1, attempts) + 1):
        if hwnd:
            break
        ensure_chat_tab(wait_seconds=max(0.5, wait))
        open_result = controller.search_and_open_room(room)
        time.sleep(wait)
        hwnd = controller.find_chat_window(room)
        if not hwnd:
            ensure_chat_tab(wait_seconds=max(0.5, wait))
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
                    "room_hwnd": int(hwnd or 0),
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
    if is_kakao_delivery_paused():
        return {
            **delivery_status(room=room, delivery_type="image"),
            "image": str(image_path),
            "skipped": True,
        }
    if not image_path.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    guard_result = ensure_kakao_ready(
        room=room,
        wait_seconds=max(15.0, open_wait * max(1, open_attempts)),
        open_room=False,
    )
    print(json.dumps({"kakao_login_guard": guard_result}, ensure_ascii=False))

    if is_kakao_delivery_paused():
        return {
            **delivery_status(room=room, delivery_type="image"),
            "image": str(image_path),
            "skipped": True,
        }

    hwnd, rect, open_result = bring_room_to_front(room, open_wait, open_attempts, open_retry_wait)
    attach_point = file_icon_point(rect)
    stale_dialogs_closed = close_owned_common_dialogs(hwnd)
    if stale_dialogs_closed:
        controller.bring_window_to_front(hwnd)
        time.sleep(0.5)
    if owned_common_dialogs(hwnd):
        raise RuntimeError("A stale KakaoTalk attachment dialog could not be closed.")
    if is_kakao_delivery_paused():
        return {
            **delivery_status(room=room, delivery_type="image"),
            "image": str(image_path),
            "skipped": True,
        }
    existing_dialogs = {info.hwnd for info in owned_file_dialogs(hwnd)}
    dialog = None
    try:
        for _attempt in range(2):
            controller.bring_window_to_front(hwnd)
            time.sleep(0.3)
            pyautogui.click(*attach_point)
            dialog = wait_for_new_file_dialog(
                hwnd,
                existing_dialogs,
                timeout_seconds=max(6.0, open_wait * 3),
            )
            if dialog is not None:
                break
        if dialog is None:
            raise RuntimeError("KakaoTalk file attachment dialog did not open.")

        if not submit_file_dialog(dialog.hwnd, image_path, timeout_seconds=max(6.0, open_wait * 3)):
            raise RuntimeError("KakaoTalk file attachment dialog did not close after selecting the image.")

        # Current PC Kakao sends the selected image as soon as the file dialog
        # accepts it. Wait for upload completion before sending the text body.
        time.sleep(send_wait)
        controller.bring_window_to_front(hwnd)
        time.sleep(0.4)
        if owned_common_dialogs(hwnd):
            raise RuntimeError("KakaoTalk left a blocking dialog open after image delivery.")
    except Exception:
        close_owned_common_dialogs(hwnd)
        controller.bring_window_to_front(hwnd)
        raise

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
        "file_dialog_hwnd": dialog.hwnd if dialog else 0,
        "dialog_closed": True,
        "stale_dialogs_closed": stale_dialogs_closed,
        "file_icon_point": list(attach_point),
        "send_button_click_required": False,
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
