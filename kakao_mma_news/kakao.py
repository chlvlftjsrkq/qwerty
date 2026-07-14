from __future__ import annotations

import platform
import subprocess
import time
from pathlib import Path

from .config import Config
from .delivery_control import is_kakao_delivery_paused


def split_message(message: str, max_chars: int) -> list[str]:
    if len(message) <= max_chars:
        return [message]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in message.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > max_chars:
            chunks.append("\n".join(current).strip())
            current = []
            current_len = 0
        if line_len > max_chars:
            for start in range(0, len(line), max_chars):
                chunks.append(line[start : start + max_chars])
            continue
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def _launch_kakao(config: Config) -> None:
    path = Path(config.kakao_app_path)
    if path.exists():
        subprocess.Popen([str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(config.kakao_wait_seconds)


def _activate_kakao_window(config: Config) -> bool:
    try:
        import pygetwindow as gw
    except ImportError:
        return False

    titles = [title.lower() for title in config.kakao_window_titles]
    for window in gw.getAllWindows():
        window_title = (window.title or "").lower()
        if any(title.lower() in window_title for title in titles):
            try:
                if window.isMinimized:
                    window.restore()
                window.activate()
                time.sleep(config.kakao_step_delay_seconds)
                return True
            except Exception:
                continue
    return False


def post_to_kakao(config: Config, message: str) -> bool:
    if is_kakao_delivery_paused():
        return False
    if platform.system() != "Windows":
        raise RuntimeError("PC 카카오톡 자동 게시는 Windows GUI 환경에서만 실행할 수 있습니다.")
    if not config.target_chatroom:
        raise RuntimeError("TARGET_CHATROOM이 비어 있습니다.")

    try:
        import pyautogui
        import pyperclip
    except ImportError as exc:
        raise RuntimeError("pyautogui, pyperclip, pygetwindow를 설치하세요.") from exc

    pyautogui.FAILSAFE = True
    _launch_kakao(config)
    if is_kakao_delivery_paused():
        return False
    focused = _activate_kakao_window(config)
    if not focused:
        raise RuntimeError(
            "카카오톡 창을 찾거나 포커스하지 못했습니다. PC 카카오톡을 로그인 상태로 켜 둔 뒤 다시 실행하세요."
        )

    if config.kakao_search_click_x is not None and config.kakao_search_click_y is not None:
        pyautogui.click(config.kakao_search_click_x, config.kakao_search_click_y)
    elif config.kakao_search_hotkey:
        pyautogui.hotkey(*config.kakao_search_hotkey)
    time.sleep(config.kakao_step_delay_seconds)

    pyperclip.copy(config.target_chatroom)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(config.kakao_step_delay_seconds)
    pyautogui.press("enter")
    time.sleep(config.kakao_wait_seconds)

    if config.kakao_message_click_x is not None and config.kakao_message_click_y is not None:
        pyautogui.click(config.kakao_message_click_x, config.kakao_message_click_y)
        time.sleep(config.kakao_step_delay_seconds)

    chunks = split_message(message, config.kakao_max_chunk_chars)
    for index, chunk in enumerate(chunks, start=1):
        if is_kakao_delivery_paused():
            return False
        body = chunk if len(chunks) == 1 else f"({index}/{len(chunks)})\n{chunk}"
        pyperclip.copy(body)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(config.kakao_step_delay_seconds)
        if config.kakao_send_enter:
            pyautogui.press("enter")
        time.sleep(config.kakao_step_delay_seconds)
    return True
