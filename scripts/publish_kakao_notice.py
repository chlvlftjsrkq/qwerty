from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pyautogui
import pyperclip
import win32con
import win32gui
import win32process
from PIL import Image, ImageGrab
from scripts.kakao_login_guard import ensure_kakao_ready

try:
    from kakao_mcp import config, controller
except ModuleNotFoundError as exc:
    config = None
    controller = None
    KAKAO_MCP_IMPORT_ERROR: ModuleNotFoundError | None = exc
else:
    KAKAO_MCP_IMPORT_ERROR = None

from kakao_mma_news.delivery_control import delivery_status, is_kakao_delivery_paused
from kakao_mma_news.kakao import split_message


pyautogui.FAILSAFE = False

MENU_CLASS = "EVA_Menu"
WINDOW_CLASS = "EVA_Window_Dblclk"


@dataclass(frozen=True)
class Box:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register the latest KakaoTalk briefing as the room notice and add its audio link as a comment."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish = subparsers.add_parser("publish")
    publish.add_argument("--room", required=True)
    publish.add_argument("--notice-file", required=True)
    publish.add_argument("--comment-file", required=True)
    publish.add_argument("--max-chars", type=int, default=3000)
    publish.add_argument("--wait-seconds", type=float, default=8.0)
    publish.add_argument("--failure-screenshot", default="")

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--room", required=True)
    inspect.add_argument("--wait-seconds", type=float, default=5.0)
    return parser.parse_args()


def window_box(hwnd: int) -> Box:
    return Box(*win32gui.GetWindowRect(hwnd))


def window_pid(hwnd: int) -> int:
    return int(win32process.GetWindowThreadProcessId(hwnd)[1])


def enum_visible_windows(predicate: Callable[[int], bool]) -> list[int]:
    found: list[int] = []

    def callback(hwnd: int, _extra: object) -> bool:
        if win32gui.IsWindowVisible(hwnd) and predicate(hwnd):
            found.append(int(hwnd))
        return True

    win32gui.EnumWindows(callback, None)
    return found


def wait_for_value(factory: Callable[[], object], timeout: float, interval: float = 0.1) -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = factory()
        if value:
            return value
        time.sleep(interval)
    return factory()


def prepare_notice_text(text: str, max_chars: int) -> str:
    body = text.strip()
    if not body:
        raise ValueError("The briefing notice is empty.")
    chunks = split_message(body, max_chars)
    if len(chunks) != 1:
        raise RuntimeError(
            f"The briefing was split into {len(chunks)} KakaoTalk messages, so a complete single-message notice cannot be selected safely."
        )
    return chunks[0]


def notice_marker(text: str) -> str:
    for line in text.splitlines():
        marker = " ".join(line.split()).strip()
        if marker:
            return marker[:120]
    raise ValueError("The briefing notice has no visible marker.")


def _is_yellow(pixel: tuple[int, ...]) -> bool:
    red, green, blue = pixel[:3]
    return red > 150 and green > 130 and blue < 75 and 0 < red - green < 70


def _is_near_white(pixel: tuple[int, ...]) -> bool:
    red, green, blue = pixel[:3]
    return red >= 248 and green >= 248 and blue >= 248


def _row_bands(rows: list[tuple[int, int, int]]) -> list[Box]:
    bands: list[list[tuple[int, int, int]]] = []
    for row in rows:
        if not bands or row[0] > bands[-1][-1][0] + 1:
            bands.append([row])
        else:
            bands[-1].append(row)
    return [
        Box(
            min(row[1] for row in band),
            band[0][0],
            max(row[2] for row in band) + 1,
            band[-1][0] + 1,
        )
        for band in bands
    ]


def find_latest_outgoing_bubble(image: Image.Image, chat_region: Box) -> Box | None:
    pixels = image.convert("RGB").load()
    minimum_pixels = max(24, int(chat_region.width * 0.07))
    rows: list[tuple[int, int, int]] = []
    for y in range(chat_region.top, chat_region.bottom):
        matches = [x for x in range(chat_region.left, chat_region.right) if _is_yellow(pixels[x, y])]
        if len(matches) >= minimum_pixels and max(matches) >= chat_region.right - 90:
            rows.append((y, min(matches), max(matches)))

    candidates = [
        band
        for band in _row_bands(rows)
        if band.height >= 14 and band.width >= 80 and band.right >= chat_region.right - 90
    ]
    return max(candidates, key=lambda band: band.bottom) if candidates else None


def find_latest_notice_action_band(image: Image.Image, chat_region: Box) -> Box | None:
    pixels = image.convert("RGB").load()
    minimum_pixels = max(80, min(150, int(chat_region.width * 0.28)))
    rows: list[tuple[int, int, int]] = []
    for y in range(chat_region.top, chat_region.bottom):
        matches = [x for x in range(chat_region.left, chat_region.right) if _is_near_white(pixels[x, y])]
        if (
            len(matches) >= minimum_pixels
            and max(matches) >= chat_region.right - 55
            and min(matches) >= chat_region.left + int(chat_region.width * 0.25)
        ):
            rows.append((y, min(matches), max(matches)))

    candidates = [
        band
        for band in _row_bands(rows)
        if band.height >= 18 and band.width >= 150 and band.right >= chat_region.right - 55
    ]
    return max(candidates, key=lambda band: band.bottom) if candidates else None


def find_menu_separator_rows(image: Image.Image) -> list[int]:
    pixels = image.convert("RGB").load()
    left = max(2, image.width // 10)
    right = min(image.width - 2, image.width - image.width // 10)
    minimum_pixels = max(20, int((right - left) * 0.7))
    matching_rows: list[int] = []
    for y in range(1, image.height - 1):
        matches = 0
        for x in range(left, right):
            red, green, blue = pixels[x, y]
            if 225 <= red <= 250 and abs(red - green) <= 3 and abs(green - blue) <= 3:
                matches += 1
        if matches >= minimum_pixels:
            matching_rows.append(y)

    groups: list[list[int]] = []
    for row in matching_rows:
        if not groups or row > groups[-1][-1] + 1:
            groups.append([row])
        else:
            groups[-1].append(row)
    return [round(sum(group) / len(group)) for group in groups]


def find_notice_menu_center(image: Image.Image) -> tuple[int, int] | None:
    separators = find_menu_separator_rows(image)
    if len(separators) < 3:
        return None

    notice_top = separators[1]
    notice_bottom = separators[2]
    row_height = notice_bottom - notice_top
    if not 20 <= row_height <= 60:
        return None
    return image.width // 2, round((notice_top + notice_bottom) / 2)


def room_and_chat_region(room: str, wait_seconds: float) -> tuple[int, int, Box, Box, dict]:
    if config is None or controller is None:
        raise RuntimeError(
            "kakaotalk-mcp is unavailable in this Python environment. "
            "Run this command with the workflow PYTHON_EXE."
        ) from KAKAO_MCP_IMPORT_ERROR
    guard = ensure_kakao_ready(room=room, wait_seconds=max(10.0, wait_seconds), open_room=False)
    hwnd = controller.find_chat_window(room)
    if not hwnd:
        guard = ensure_kakao_ready(room=room, wait_seconds=max(10.0, wait_seconds), open_room=True)
        hwnd = controller.find_chat_window(room)
    if not hwnd:
        raise RuntimeError(f"The exact KakaoTalk room window is not open: {room}")

    controller.bring_window_to_front(hwnd)
    time.sleep(0.4)
    if win32gui.IsIconic(hwnd):
        raise RuntimeError(f"The KakaoTalk room could not be restored: {room}")

    list_hwnd = controller.find_child_window_recursive(hwnd, config.KAKAO_LIST_CONTROL_CLASS)
    if not list_hwnd or not win32gui.IsWindowVisible(list_hwnd):
        raise RuntimeError(f"The KakaoTalk chat list is not visible: {room}")

    room_box = window_box(hwnd)
    list_box = window_box(list_hwnd)
    if room_box.width < 320 or room_box.height < 500 or list_box.width < 250 or list_box.height < 250:
        raise RuntimeError(f"The KakaoTalk room geometry is unsafe: room={room_box}, chat={list_box}")
    relative = Box(
        list_box.left - room_box.left,
        list_box.top - room_box.top,
        list_box.right - room_box.left,
        list_box.bottom - room_box.top,
    )
    return int(hwnd), int(list_hwnd), room_box, relative, guard


def capture_room(room_box: Box) -> Image.Image:
    return ImageGrab.grab((room_box.left, room_box.top, room_box.right, room_box.bottom)).convert("RGB")


def clear_chat_selection(room_hwnd: int, list_hwnd: int) -> None:
    controller.bring_window_to_front(room_hwnd)
    time.sleep(0.2)
    box = window_box(list_hwnd)
    pyautogui.click(box.left + 18, (box.top + box.bottom) // 2)
    time.sleep(0.2)


def kakao_menus(pid: int) -> list[int]:
    return enum_visible_windows(
        lambda hwnd: win32gui.GetClassName(hwnd) == MENU_CLASS and window_pid(hwnd) == pid
    )


def open_full_message_menu(
    room_hwnd: int,
    list_hwnd: int,
    room_box: Box,
    chat_region: Box,
    wait_seconds: float,
) -> tuple[int, Box, Box]:
    pid = window_pid(room_hwnd)
    for _attempt in range(3):
        clear_chat_selection(room_hwnd, list_hwnd)
        image = capture_room(room_box)
        bubble = find_latest_outgoing_bubble(image, chat_region)
        if bubble is None:
            raise RuntimeError("The latest outgoing KakaoTalk message bubble was not found.")

        click_x = room_box.left + bubble.center[0]
        click_y = room_box.top + bubble.center[1]
        pyautogui.rightClick(click_x, click_y)
        menu_handles = wait_for_value(lambda: kakao_menus(pid), min(wait_seconds, 2.0))
        menus = list(menu_handles) if isinstance(menu_handles, list) else []
        if len(menus) == 1:
            menu_box = window_box(menus[0])
            if 80 <= menu_box.width <= 240 and 240 <= menu_box.height <= 600:
                return menus[0], menu_box, bubble

        clear_chat_selection(room_hwnd, list_hwnd)
    raise RuntimeError("The full KakaoTalk message menu did not appear.")


def confirmation_dialogs(pid: int, excluded: set[int]) -> list[int]:
    def matches(hwnd: int) -> bool:
        if hwnd in excluded or window_pid(hwnd) != pid:
            return False
        if win32gui.GetClassName(hwnd) != WINDOW_CLASS or win32gui.GetWindowText(hwnd):
            return False
        box = window_box(hwnd)
        return 220 <= box.width <= 420 and 120 <= box.height <= 260 and 1.35 <= box.width / box.height <= 2.5

    return enum_visible_windows(matches)


def register_latest_message_as_notice(
    room_hwnd: int,
    list_hwnd: int,
    room_box: Box,
    chat_region: Box,
    wait_seconds: float,
) -> dict:
    pid = window_pid(room_hwnd)
    before = set(confirmation_dialogs(pid, set()))
    menu_hwnd, menu_box, bubble = open_full_message_menu(
        room_hwnd, list_hwnd, room_box, chat_region, wait_seconds
    )
    menu_image = ImageGrab.grab(
        (menu_box.left, menu_box.top, menu_box.right, menu_box.bottom)
    ).convert("RGB")
    notice_center = find_notice_menu_center(menu_image)
    if notice_center is None:
        win32gui.PostMessage(menu_hwnd, win32con.WM_CLOSE, 0, 0)
        raise RuntimeError("The KakaoTalk notice menu row could not be identified safely.")
    notice_x = menu_box.left + notice_center[0]
    notice_y = menu_box.top + notice_center[1]
    pyautogui.click(notice_x, notice_y)

    value = wait_for_value(
        lambda: confirmation_dialogs(pid, before | {room_hwnd, menu_hwnd}),
        min(wait_seconds, 3.0),
    )
    dialogs = list(value) if isinstance(value, list) else []
    if len(dialogs) != 1:
        for dialog in dialogs:
            if win32gui.IsWindow(dialog):
                win32gui.PostMessage(dialog, win32con.WM_CLOSE, 0, 0)
        raise RuntimeError(f"Expected one KakaoTalk notice confirmation dialog, found {len(dialogs)}.")

    dialog = dialogs[0]
    dialog_box = window_box(dialog)
    win32gui.SetForegroundWindow(dialog)
    pyautogui.press("enter")
    closed = wait_for_value(
        lambda: not win32gui.IsWindow(dialog) or not win32gui.IsWindowVisible(dialog),
        min(wait_seconds, 3.0),
    )
    if not closed:
        if win32gui.IsWindow(dialog):
            win32gui.PostMessage(dialog, win32con.WM_CLOSE, 0, 0)
        raise RuntimeError("The KakaoTalk notice confirmation dialog did not close.")
    time.sleep(0.6)
    return {
        "bubble": list((bubble.left, bubble.top, bubble.right, bubble.bottom)),
        "menu": list((menu_box.left, menu_box.top, menu_box.right, menu_box.bottom)),
        "notice_menu_point": [notice_x, notice_y],
        "dialog": list((dialog_box.left, dialog_box.top, dialog_box.right, dialog_box.bottom)),
    }


def detail_windows(room: str, room_hwnd: int) -> list[tuple[int, int]]:
    pid = window_pid(room_hwnd)
    details: list[tuple[int, int]] = []
    for hwnd in enum_visible_windows(
        lambda candidate: candidate != room_hwnd
        and window_pid(candidate) == pid
        and win32gui.GetClassName(candidate) == WINDOW_CLASS
        and win32gui.GetWindowText(candidate) == room
    ):
        classes: list[tuple[int, str, str]] = []

        def callback(child: int, _extra: object) -> bool:
            classes.append((int(child), win32gui.GetClassName(child), win32gui.GetWindowText(child)))
            return True

        win32gui.EnumChildWindows(hwnd, callback, None)
        rich_edits = [child for child, class_name, _text in classes if class_name == "RichEdit20W"]
        texts = {text for _child, _class_name, text in classes}
        if len(rich_edits) == 1 and {"MoimContentWnd", "MoimPostWnd"}.issubset(texts):
            details.append((int(hwnd), rich_edits[0]))
    return details


def close_kakao_transients(room: str) -> None:
    """Close only notice-related KakaoTalk popups left by an interrupted run."""
    room_hwnd = controller.find_chat_window(room)
    if not room_hwnd:
        return

    pid = window_pid(room_hwnd)
    handles = set(kakao_menus(pid))
    handles.update(detail_hwnd for detail_hwnd, _edit_hwnd in detail_windows(room, room_hwnd))
    for hwnd in handles:
        if hwnd != room_hwnd and win32gui.IsWindow(hwnd):
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    if handles:
        time.sleep(0.3)


def open_notice_detail(
    room: str,
    room_hwnd: int,
    list_hwnd: int,
    room_box: Box,
    chat_region: Box,
    wait_seconds: float,
) -> tuple[int, int, Box]:
    controller.bring_window_to_front(room_hwnd)
    time.sleep(0.4)
    image = capture_room(room_box)
    action_band = find_latest_notice_action_band(image, chat_region)
    if action_band is None:
        clear_chat_selection(room_hwnd, list_hwnd)
        pyautogui.press("end")
        time.sleep(0.4)
        image = capture_room(room_box)
        action_band = find_latest_notice_action_band(image, chat_region)
    if action_band is None:
        raise RuntimeError("The latest KakaoTalk notice action card was not found.")

    pyautogui.click(
        room_box.left + action_band.center[0],
        room_box.top + action_band.center[1],
    )
    value = wait_for_value(lambda: detail_windows(room, room_hwnd), min(wait_seconds, 3.0))
    details = list(value) if isinstance(value, list) else []
    if len(details) != 1:
        raise RuntimeError(f"Expected one KakaoTalk notice detail window, found {len(details)}.")
    return details[0][0], details[0][1], action_band


def normalize_control_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def write_notice_comment_input(
    detail_hwnd: int,
    edit_hwnd: int,
    body: str,
    wait_seconds: float,
) -> None:
    win32gui.SetForegroundWindow(detail_hwnd)
    edit_box = window_box(edit_hwnd)
    previous_clipboard = pyperclip.paste()
    try:
        for _attempt in range(2):
            pyautogui.click(*edit_box.center)
            time.sleep(0.2)
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("backspace")
            pyperclip.copy(body)
            pyautogui.hotkey("ctrl", "v")
            written = wait_for_value(
                lambda: normalize_control_text(win32gui.GetWindowText(edit_hwnd))
                == normalize_control_text(body),
                min(wait_seconds, 2.0),
            )
            if written:
                return
    finally:
        time.sleep(0.1)
        pyperclip.copy(previous_clipboard)
    raise RuntimeError("The notice comment was not written to the KakaoTalk input control.")


def post_notice_comment(detail_hwnd: int, edit_hwnd: int, comment: str, wait_seconds: float) -> None:
    body = comment.strip()
    if not body:
        raise ValueError("The notice comment is empty.")
    if len(body) > 1000:
        raise ValueError("The notice comment is unexpectedly long.")

    write_notice_comment_input(detail_hwnd, edit_hwnd, body, wait_seconds)
    pyautogui.press("enter")
    cleared = wait_for_value(
        lambda: win32gui.IsWindow(edit_hwnd)
        and not normalize_control_text(win32gui.GetWindowText(edit_hwnd)),
        min(wait_seconds, 3.0),
    )
    if not cleared:
        raise RuntimeError("The KakaoTalk notice comment input did not clear after submission.")
    time.sleep(0.4)


def verify_notice_event(room: str, room_hwnd: int, list_hwnd: int, marker: str) -> bool:
    read_result = controller.read_chat_messages(room)
    raw_text = read_result.get("raw_text") or ""
    expected = f"게시판 '공지': {marker}"
    verified = read_result.get("success") is True and expected in raw_text
    if win32gui.IsWindow(room_hwnd) and win32gui.IsWindow(list_hwnd):
        clear_chat_selection(room_hwnd, list_hwnd)
    return verified


def save_failure_screenshot(path: Path, room: str) -> str:
    hwnd = controller.find_chat_window(room)
    if not hwnd:
        return ""
    try:
        controller.bring_window_to_front(hwnd)
        time.sleep(0.2)
        path.parent.mkdir(parents=True, exist_ok=True)
        box = window_box(hwnd)
        capture_room(box).save(path)
        return str(path)
    except Exception:
        return ""


def inspect_room(room: str, wait_seconds: float) -> dict:
    room_hwnd, list_hwnd, room_box, chat_region, guard = room_and_chat_region(room, wait_seconds)
    image = capture_room(room_box)
    bubble = find_latest_outgoing_bubble(image, chat_region)
    action = find_latest_notice_action_band(image, chat_region)
    return {
        "room": room,
        "room_hwnd": room_hwnd,
        "list_hwnd": list_hwnd,
        "room_rect": list((room_box.left, room_box.top, room_box.right, room_box.bottom)),
        "chat_rect_relative": list((chat_region.left, chat_region.top, chat_region.right, chat_region.bottom)),
        "latest_outgoing_bubble": list((bubble.left, bubble.top, bubble.right, bubble.bottom)) if bubble else None,
        "latest_notice_action_band": list((action.left, action.top, action.right, action.bottom)) if action else None,
        "guard": guard,
        "clicked": False,
    }


def publish_notice(
    room: str,
    notice_path: Path,
    comment_path: Path,
    max_chars: int,
    wait_seconds: float,
) -> dict:
    if is_kakao_delivery_paused():
        return {
            **delivery_status(room=room, delivery_type="briefing_notice"),
            "skipped": True,
        }
    if not notice_path.is_file():
        raise FileNotFoundError(f"The briefing notice file was not found: {notice_path}")
    if not comment_path.is_file():
        raise FileNotFoundError(f"The podcast comment file was not found: {comment_path}")

    notice_text = prepare_notice_text(notice_path.read_text(encoding="utf-8-sig"), max_chars)
    comment_text = comment_path.read_text(encoding="utf-8-sig").strip()
    marker = notice_marker(notice_text)
    room_hwnd, list_hwnd, room_box, chat_region, guard = room_and_chat_region(room, wait_seconds)
    close_kakao_transients(room)
    controller.bring_window_to_front(room_hwnd)
    time.sleep(0.3)

    initial_read = controller.read_chat_messages(room)
    if initial_read.get("success") is not True or marker not in (initial_read.get("raw_text") or ""):
        raise RuntimeError("The just-posted briefing marker was not found in the KakaoTalk room.")
    clear_chat_selection(room_hwnd, list_hwnd)

    notice_state = register_latest_message_as_notice(
        room_hwnd,
        list_hwnd,
        room_box,
        chat_region,
        wait_seconds,
    )
    detail_hwnd, edit_hwnd, action_band = open_notice_detail(
        room,
        room_hwnd,
        list_hwnd,
        room_box,
        chat_region,
        wait_seconds,
    )
    try:
        post_notice_comment(detail_hwnd, edit_hwnd, comment_text, wait_seconds)
    finally:
        if win32gui.IsWindow(detail_hwnd):
            win32gui.PostMessage(detail_hwnd, win32con.WM_CLOSE, 0, 0)
            time.sleep(0.3)

    verified = verify_notice_event(room, room_hwnd, list_hwnd, marker)
    if not verified:
        raise RuntimeError("The new KakaoTalk notice event could not be verified in chat history.")
    return {
        "room": room,
        "notice_file": str(notice_path),
        "comment_file": str(comment_path),
        "marker": marker,
        "notice_registered": True,
        "comment_registered": True,
        "notice_event_verified": True,
        "notice_action_band": list((action_band.left, action_band.top, action_band.right, action_band.bottom)),
        "window_state": notice_state,
        "guard": guard,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    try:
        if args.command == "inspect":
            result = inspect_room(args.room, args.wait_seconds)
        else:
            result = publish_notice(
                room=args.room,
                notice_path=Path(args.notice_file).resolve(),
                comment_path=Path(args.comment_file).resolve(),
                max_chars=args.max_chars,
                wait_seconds=args.wait_seconds,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        screenshot = ""
        if args.command == "publish" and args.failure_screenshot:
            screenshot = save_failure_screenshot(Path(args.failure_screenshot).resolve(), args.room)
        if args.command == "publish":
            try:
                close_kakao_transients(args.room)
            except Exception:
                pass
        print(
            json.dumps(
                {
                    "room": args.room,
                    "success": False,
                    "error": str(exc),
                    "failure_screenshot": screenshot,
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
