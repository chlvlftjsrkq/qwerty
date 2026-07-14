from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PAUSE_FILE_ENV = "KAKAO_DELIVERY_PAUSE_FILE"


def pause_file_path() -> Path:
    configured = os.getenv(PAUSE_FILE_ENV, "").strip()
    if configured:
        return Path(os.path.expandvars(configured)).expanduser()

    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        base_dir = Path(local_app_data)
    else:
        base_dir = Path.home() / "AppData" / "Local"
    return base_dir / "qwerty" / "kakao-delivery.pause"


def is_kakao_delivery_paused() -> bool:
    return pause_file_path().is_file()


def delivery_status(*, room: str = "", delivery_type: str = "") -> dict[str, Any]:
    pause_path = pause_file_path()
    paused = pause_path.is_file()
    result: dict[str, Any] = {
        "delivery_paused": paused,
        "delivery_status": "paused" if paused else "enabled",
        "pause_file": str(pause_path),
    }
    if room:
        result["room"] = room
    if delivery_type:
        result["delivery_type"] = delivery_type
    return result


def pause_kakao_delivery() -> Path:
    pause_path = pause_file_path()
    pause_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = pause_path.with_suffix(pause_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(
            {
                "paused": True,
                "paused_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary_path.replace(pause_path)
    return pause_path


def resume_kakao_delivery() -> Path:
    pause_path = pause_file_path()
    try:
        pause_path.unlink()
    except FileNotFoundError:
        pass
    return pause_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Control qwerty KakaoTalk delivery.")
    parser.add_argument("action", choices=("status", "pause", "resume", "toggle"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.action == "pause":
        pause_kakao_delivery()
    elif args.action == "resume":
        resume_kakao_delivery()
    elif args.action == "toggle":
        if is_kakao_delivery_paused():
            resume_kakao_delivery()
        else:
            pause_kakao_delivery()
    print(json.dumps(delivery_status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
