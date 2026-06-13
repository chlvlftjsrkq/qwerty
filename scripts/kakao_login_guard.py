from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
for candidate in (
    ROOT_DIR / ".venv" / "Lib" / "site-packages",
    Path.home()
    / ".cache"
    / "codex-runtimes"
    / "codex-primary-runtime"
    / "dependencies"
    / "python"
    / "Lib"
    / "site-packages",
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import pyautogui
import pyperclip
import win32api
import win32con
import win32gui

try:
    from kakao_mcp import controller
except Exception:
    controller = None


APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "qwerty"
SECRET_PATH = APP_DIR / "kakao-password.dpapi"
DEFAULT_KAKAO_PATHS = (
    Path(r"C:\Program Files\Kakao\KakaoTalk\KakaoTalk.exe"),
    Path(r"C:\Program Files (x86)\Kakao\KakaoTalk\KakaoTalk.exe"),
)

pyautogui.FAILSAFE = False


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.c_void_p),
    ]


CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPT32 = ctypes.windll.crypt32
KERNEL32 = ctypes.windll.kernel32
KERNEL32.LocalFree.argtypes = [ctypes.c_void_p]
KERNEL32.LocalFree.restype = ctypes.c_void_p


def _blob_from_bytes(data: bytes) -> tuple[DATA_BLOB, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.c_void_p)), buffer


def _bytes_from_blob(blob: DATA_BLOB) -> bytes:
    if not blob.pbData or blob.cbData == 0:
        return b""
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        KERNEL32.LocalFree(blob.pbData)


def protect_secret(text: str) -> bytes:
    input_blob, _buffer = _blob_from_bytes(text.encode("utf-8"))
    output_blob = DATA_BLOB()
    ok = CRYPT32.CryptProtectData(
        ctypes.byref(input_blob),
        "qwerty KakaoTalk password",
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    if not ok:
        raise ctypes.WinError()
    return _bytes_from_blob(output_blob)


def unprotect_secret(data: bytes) -> str:
    input_blob, _buffer = _blob_from_bytes(data)
    output_blob = DATA_BLOB()
    ok = CRYPT32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    )
    if not ok:
        raise ctypes.WinError()
    return _bytes_from_blob(output_blob).decode("utf-8")


def store_password_from_clipboard(secret_path: Path = SECRET_PATH) -> dict[str, Any]:
    password = pyperclip.paste().rstrip("\r\n")
    if not password:
        raise RuntimeError("Clipboard is empty; KakaoTalk password was not stored.")

    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(base64.b64encode(protect_secret(password)).decode("ascii"), encoding="ascii")
    return {"stored": True, "path": str(secret_path)}


def load_password(secret_path: Path = SECRET_PATH) -> str:
    if not secret_path.exists():
        raise FileNotFoundError(f"KakaoTalk password secret was not found: {secret_path}")
    encrypted = base64.b64decode(secret_path.read_text(encoding="ascii").strip())
    return unprotect_secret(encrypted)


def _enum_top_windows() -> list[int]:
    result: list[int] = []

    def cb(hwnd: int, _extra: object) -> bool:
        if win32gui.IsWindowVisible(hwnd):
            result.append(hwnd)
        return True

    win32gui.EnumWindows(cb, None)
    return result


def _window_text(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def _window_class(hwnd: int) -> str:
    try:
        return win32gui.GetClassName(hwnd)
    except Exception:
        return ""


def _visible_child_windows(root_hwnd: int) -> list[int]:
    result: list[int] = []

    def walk(hwnd: int) -> None:
        result.append(hwnd)
        try:
            win32gui.EnumChildWindows(hwnd, lambda child, _extra: (walk(child), True)[1], None)
        except Exception:
            pass

    walk(root_hwnd)
    return [hwnd for hwnd in result if win32gui.IsWindowVisible(hwnd)]


def _visible_child_edits(root_hwnd: int) -> list[int]:
    edits: list[int] = []
    for hwnd in _visible_child_windows(root_hwnd):
        if _window_class(hwnd) != "Edit":
            continue
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        if right - left >= 80 and bottom - top >= 14:
            edits.append(hwnd)
    return sorted(edits, key=lambda item: win32gui.GetWindowRect(item)[1])


def find_login_window() -> int | None:
    for hwnd in _enum_top_windows():
        if _window_text(hwnd) != "카카오톡":
            continue
        if _window_class(hwnd) != "EVA_Window":
            continue
        if _visible_child_edits(hwnd):
            return hwnd
    return None


def find_main_window() -> int | None:
    for hwnd in _enum_top_windows():
        if _window_text(hwnd) == "카카오톡" and _window_class(hwnd) == "EVA_Window_Dblclk":
            return hwnd
    return None


def _bring_to_front(hwnd: int) -> None:
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    pyautogui.press("alt")
    time.sleep(0.1)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.4)


def _rect_center(hwnd: int) -> tuple[int, int]:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return (left + right) // 2, (top + bottom) // 2


def launch_kakao_if_needed(wait_seconds: float = 2.0) -> None:
    if find_main_window() or find_login_window():
        return
    for path in DEFAULT_KAKAO_PATHS:
        if path.exists():
            subprocess.Popen([str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(wait_seconds)
            return


def login_with_stored_password(secret_path: Path = SECRET_PATH, wait_seconds: float = 15.0) -> dict[str, Any]:
    launch_kakao_if_needed()
    login_hwnd = find_login_window()
    if not login_hwnd:
        return {"login_needed": False, "login_attempted": False}

    password = load_password(secret_path)
    edits = _visible_child_edits(login_hwnd)
    if not edits:
        raise RuntimeError("KakaoTalk login password field was not found.")

    password_edit = edits[-1]
    _bring_to_front(login_hwnd)
    x, y = _rect_center(password_edit)

    previous_clipboard = pyperclip.paste()
    try:
        pyautogui.click(x, y)
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "a")
        pyperclip.copy(password)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.4)
        pyautogui.press("enter")

        # Some KakaoTalk builds require an explicit login button click after paste.
        left, top, right, bottom = win32gui.GetWindowRect(password_edit)
        root_left, _root_top, root_right, _root_bottom = win32gui.GetWindowRect(login_hwnd)
        pyautogui.click((root_left + root_right) // 2, bottom + 37)
    finally:
        time.sleep(0.2)
        pyperclip.copy(previous_clipboard)

    deadline = time.time() + max(1.0, wait_seconds)
    while time.time() < deadline:
        if not find_login_window():
            return {"login_needed": True, "login_attempted": True, "login_closed": True}
        time.sleep(0.5)
    return {"login_needed": True, "login_attempted": True, "login_closed": False}


def _is_chat_room_list_visible(main_hwnd: int) -> bool:
    for hwnd in _visible_child_windows(main_hwnd):
        if _window_class(hwnd) == "EVA_Window" and "ChatRoomListView" in _window_text(hwnd):
            return True
    return False


def ensure_chat_tab(wait_seconds: float = 0.5) -> bool:
    main_hwnd = find_main_window()
    if not main_hwnd:
        return False
    if _is_chat_room_list_visible(main_hwnd):
        return True

    _bring_to_front(main_hwnd)
    pyautogui.hotkey("ctrl", "2")
    time.sleep(wait_seconds)
    if _is_chat_room_list_visible(main_hwnd):
        return True

    left, top, _right, _bottom = win32gui.GetWindowRect(main_hwnd)
    # Try likely KakaoTalk sidebar positions; stop as soon as ChatRoomListView is visible.
    for offset_y in (95, 125, 155, 185, 215):
        pyautogui.click(left + 32, top + offset_y)
        time.sleep(wait_seconds)
        if _is_chat_room_list_visible(main_hwnd):
            return True
    return _is_chat_room_list_visible(main_hwnd)


def try_open_room(room: str) -> dict[str, Any]:
    if controller is None:
        return {"error": "kakao_mcp controller is not available"}
    result = controller.search_and_open_room(room)
    return result


def ensure_kakao_ready(room: str = "", wait_seconds: float = 15.0) -> dict[str, Any]:
    launch_kakao_if_needed()
    before_login_window = find_login_window() is not None
    login_result = login_with_stored_password(wait_seconds=wait_seconds) if before_login_window else {
        "login_needed": False,
        "login_attempted": False,
    }
    chat_tab_ready = ensure_chat_tab()

    room_result: dict[str, Any] | None = None
    if room:
        room_result = try_open_room(room)
        if room_result.get("success") is not True and chat_tab_ready is False:
            chat_tab_ready = ensure_chat_tab()
            room_result = try_open_room(room)

    return {
        "login_window_present_before": before_login_window,
        "login": login_result,
        "chat_tab_ready": chat_tab_ready,
        "room": room,
        "room_result": room_result,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Store and use a KakaoTalk login password via Windows DPAPI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("store-password-from-clipboard")

    login_parser = subparsers.add_parser("login")
    login_parser.add_argument("--room", default="")
    login_parser.add_argument("--wait-seconds", type=float, default=15.0)

    ready_parser = subparsers.add_parser("ensure-ready")
    ready_parser.add_argument("--room", default="")
    ready_parser.add_argument("--wait-seconds", type=float, default=15.0)

    subparsers.add_parser("status")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()

    if args.command == "store-password-from-clipboard":
        result = store_password_from_clipboard()
    elif args.command == "login":
        result = login_with_stored_password(wait_seconds=args.wait_seconds)
        if args.room:
            result["chat_tab_ready"] = ensure_chat_tab()
            result["room_result"] = try_open_room(args.room)
    elif args.command == "ensure-ready":
        result = ensure_kakao_ready(room=args.room, wait_seconds=args.wait_seconds)
    elif args.command == "status":
        result = {
            "login_window_present": find_login_window() is not None,
            "main_window_present": find_main_window() is not None,
            "secret_exists": SECRET_PATH.exists(),
        }
    else:
        raise RuntimeError(f"Unsupported command: {args.command}")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
