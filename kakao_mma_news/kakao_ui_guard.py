from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
import time
from dataclasses import dataclass
from pathlib import Path


IS_WINDOWS = sys.platform == "win32"
COMMON_DIALOG_CLASS = "#32770"
OPEN_DIALOG_TITLES = {"열기", "Open"}
GW_OWNER = 4
WM_CLOSE = 0x0010
WM_SETTEXT = 0x000C
BM_CLICK = 0x00F5


if IS_WINDOWS:
    USER32 = ctypes.windll.user32
    USER32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    USER32.EnumWindows.restype = ctypes.wintypes.BOOL
    USER32.EnumChildWindows.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p]
    USER32.EnumChildWindows.restype = ctypes.wintypes.BOOL
    USER32.GetWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint]
    USER32.GetWindow.restype = ctypes.wintypes.HWND
    USER32.GetWindowTextLengthW.argtypes = [ctypes.wintypes.HWND]
    USER32.GetWindowTextLengthW.restype = ctypes.c_int
    USER32.GetWindowTextW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.c_int]
    USER32.GetWindowTextW.restype = ctypes.c_int
    USER32.GetClassNameW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.c_int]
    USER32.GetClassNameW.restype = ctypes.c_int
    USER32.GetWindowThreadProcessId.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    ]
    USER32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
    USER32.IsWindow.argtypes = [ctypes.wintypes.HWND]
    USER32.IsWindow.restype = ctypes.wintypes.BOOL
    USER32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
    USER32.IsWindowVisible.restype = ctypes.wintypes.BOOL
    USER32.PostMessageW.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.c_uint,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    ]
    USER32.PostMessageW.restype = ctypes.wintypes.BOOL
    USER32.SendMessageW.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.c_uint,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    ]
    USER32.SendMessageW.restype = ctypes.wintypes.LPARAM
else:
    USER32 = None


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    process_id: int
    owner: int


def file_icon_point(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = rect
    if right - left < 160 or bottom - top < 60:
        raise RuntimeError(f"KakaoTalk room window is too small for image attachment: {rect}")
    return left + 87, bottom - 25


def _require_windows() -> None:
    if not IS_WINDOWS or USER32 is None:
        raise RuntimeError("KakaoTalk UI automation requires Windows.")


def _window_text(hwnd: int) -> str:
    length = int(USER32.GetWindowTextLengthW(hwnd))
    buffer = ctypes.create_unicode_buffer(max(2, length + 1))
    USER32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    USER32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def _process_id(hwnd: int) -> int:
    process_id = ctypes.wintypes.DWORD()
    USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    return int(process_id.value)


def window_info(hwnd: int) -> WindowInfo:
    _require_windows()
    return WindowInfo(
        hwnd=int(hwnd),
        title=_window_text(hwnd),
        class_name=_class_name(hwnd),
        process_id=_process_id(hwnd),
        owner=int(USER32.GetWindow(hwnd, GW_OWNER) or 0),
    )


def enum_top_windows(*, visible_only: bool = True) -> list[WindowInfo]:
    _require_windows()
    windows: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not visible_only or USER32.IsWindowVisible(hwnd):
            windows.append(window_info(int(hwnd)))
        return True

    USER32.EnumWindows(callback, 0)
    return windows


def owner_chain_contains(hwnd: int, expected_owner: int) -> bool:
    _require_windows()
    seen: set[int] = set()
    current = int(hwnd)
    while current and current not in seen:
        seen.add(current)
        current = int(USER32.GetWindow(current, GW_OWNER) or 0)
        if current == int(expected_owner):
            return True
    return False


def owned_common_dialogs(room_hwnd: int) -> list[WindowInfo]:
    """Return visible Win32 dialogs transitively owned by one Kakao room."""

    room_pid = _process_id(room_hwnd)
    return [
        info
        for info in enum_top_windows()
        if info.class_name == COMMON_DIALOG_CLASS
        and info.process_id == room_pid
        and owner_chain_contains(info.hwnd, room_hwnd)
    ]


def _enum_child_windows(hwnd: int) -> list[WindowInfo]:
    children: list[WindowInfo] = []

    @ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def callback(child_hwnd: int, _lparam: int) -> bool:
        children.append(window_info(int(child_hwnd)))
        return True

    USER32.EnumChildWindows(hwnd, callback, 0)
    return children


def file_dialog_controls(dialog_hwnd: int) -> tuple[int, int]:
    """Return the filename edit and Open button handles for a common file dialog."""

    filename_edit = 0
    open_button = 0
    for child in _enum_child_windows(dialog_hwnd):
        if not USER32.IsWindowVisible(child.hwnd):
            continue
        if child.class_name == "Edit":
            filename_edit = child.hwnd
        elif child.class_name == "Button" and (
            child.title.startswith("열기") or child.title.lower().startswith("open")
        ):
            open_button = child.hwnd
    return filename_edit, open_button


def owned_file_dialogs(room_hwnd: int) -> list[WindowInfo]:
    dialogs: list[WindowInfo] = []
    for info in owned_common_dialogs(room_hwnd):
        filename_edit, open_button = file_dialog_controls(info.hwnd)
        if info.title in OPEN_DIALOG_TITLES and filename_edit and open_button:
            dialogs.append(info)
    return dialogs


def wait_for_new_file_dialog(
    room_hwnd: int,
    existing_handles: set[int],
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.1,
) -> WindowInfo | None:
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    while time.monotonic() < deadline:
        for info in owned_file_dialogs(room_hwnd):
            if info.hwnd not in existing_handles:
                return info
        time.sleep(max(0.02, poll_seconds))
    return None


def window_is_visible(hwnd: int) -> bool:
    _require_windows()
    return bool(USER32.IsWindow(hwnd) and USER32.IsWindowVisible(hwnd))


def wait_for_window_closed(hwnd: int, *, timeout_seconds: float, poll_seconds: float = 0.1) -> bool:
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    while time.monotonic() < deadline:
        if not window_is_visible(hwnd):
            return True
        time.sleep(max(0.02, poll_seconds))
    return not window_is_visible(hwnd)


def submit_file_dialog(dialog_hwnd: int, image_path: Path, *, timeout_seconds: float = 6.0) -> bool:
    """Set the filename control directly and wait until Kakao accepts the file."""

    filename_edit, open_button = file_dialog_controls(dialog_hwnd)
    if not filename_edit or not open_button:
        raise RuntimeError("KakaoTalk file dialog controls were not found.")
    resolved_path = str(image_path.resolve())
    path_buffer = ctypes.create_unicode_buffer(resolved_path)
    USER32.SendMessageW(filename_edit, WM_SETTEXT, 0, ctypes.addressof(path_buffer))
    USER32.SendMessageW(open_button, BM_CLICK, 0, 0)
    return wait_for_window_closed(dialog_hwnd, timeout_seconds=timeout_seconds)


def close_owned_common_dialogs(room_hwnd: int, *, timeout_seconds: float = 2.0) -> list[int]:
    """Close only modal dialogs owned by the target room, deepest owners first."""

    dialogs = owned_common_dialogs(room_hwnd)

    def owner_depth(info: WindowInfo) -> int:
        depth = 0
        current = info.hwnd
        seen: set[int] = set()
        while current and current not in seen:
            seen.add(current)
            current = int(USER32.GetWindow(current, GW_OWNER) or 0)
            depth += 1
            if current == room_hwnd:
                break
        return depth

    closed: list[int] = []
    for info in sorted(dialogs, key=owner_depth, reverse=True):
        if window_is_visible(info.hwnd):
            USER32.PostMessageW(info.hwnd, WM_CLOSE, 0, 0)
            closed.append(info.hwnd)

    deadline = time.monotonic() + max(0.1, timeout_seconds)
    while time.monotonic() < deadline and any(window_is_visible(hwnd) for hwnd in closed):
        time.sleep(0.05)
    return closed


def clear_blocking_dialogs_or_raise(room_hwnd: int) -> None:
    """Prevent text from being pasted into a leftover Kakao attachment dialog."""

    dialogs = owned_common_dialogs(room_hwnd)
    if not dialogs:
        return
    details = [f"{info.title or '(untitled)'}[{info.hwnd}]" for info in dialogs]
    close_owned_common_dialogs(room_hwnd)
    raise RuntimeError(
        "KakaoTalk has a blocking dialog owned by the target room; "
        f"closed it and aborted text delivery: {', '.join(details)}"
    )
