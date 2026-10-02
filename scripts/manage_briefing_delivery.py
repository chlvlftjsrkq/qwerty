from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from kakao_mma_news.briefing_delivery import bundle_path, prepare_briefing, send_saved_briefing, status_catalog


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Save generated briefings and resume only unfinished delivery steps.")
    commands = parser.add_subparsers(dest="action", required=True)
    prepare = commands.add_parser("prepare")
    for name in ("episode-id", "date-label", "agency", "room", "summary", "image", "podcast-message", "audio"):
        prepare.add_argument("--" + name, required=True)
    prepare.add_argument("--source-run-id", default="")
    for name in ("image", "summary", "podcast"):
        prepare.add_argument("--send-" + name, choices=("true", "false"), default="true")
    send = commands.add_parser("send")
    send.add_argument("--bundle-id", required=True)
    send.add_argument("--mcp-command", default="")
    commands.add_parser("status")
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            result = prepare_briefing(
                episode_id=args.episode_id, date_label=args.date_label, agency=args.agency, room=args.room,
                summary=Path(args.summary), image=Path(args.image), podcast_message=Path(args.podcast_message), audio=Path(args.audio),
                source_run_id=args.source_run_id, send_image=args.send_image == "true", send_summary=args.send_summary == "true", send_podcast=args.send_podcast == "true",
            )
        elif args.action == "send":
            result = send_saved_briefing(bundle_path(args.bundle_id), ROOT_DIR, args.mcp_command)
        else:
            result = status_catalog()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if args.action != "send" or result["status"] == "completed" else 1
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
