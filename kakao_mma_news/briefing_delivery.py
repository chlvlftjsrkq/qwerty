from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .delivery_control import is_kakao_delivery_paused, kakao_delivery_lock
from .kakao import split_message


STAGE_LABELS = {"image": "이미지", "summary": "브리핑 본문", "notice": "공지·음성 댓글(본문 재게시)", "podcast": "음성 링크"}


def outbox_directory() -> Path:
    configured = os.getenv("QWERTY_BRIEFING_OUTBOX", "").strip()
    if configured:
        return Path(os.path.expandvars(configured)).expanduser()
    return Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "qwerty" / "briefing-outbox"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary_path = Path(stream.name)
        json.dump(state, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def load_state(path: Path) -> dict[str, Any]:
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema_version") != 1 or not isinstance(state.get("steps"), list):
        raise ValueError("Unsupported briefing delivery record.")
    return state


def bundle_path(bundle_id: str, outbox: Path | None = None) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", bundle_id):
        raise ValueError("Invalid briefing bundle id.")
    return (outbox or outbox_directory()) / bundle_id / "delivery.json"


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_briefing(
    *, episode_id: str, date_label: str, agency: str, room: str,
    summary: Path, image: Path, podcast_message: Path, audio: Path,
    send_image: bool = True, send_summary: bool = True, send_podcast: bool = True,
    source_run_id: str = "", outbox: Path | None = None,
) -> dict[str, Any]:
    if not room.strip():
        raise ValueError("A target chat room is required.")
    root = outbox or outbox_directory()
    bundle_id = "briefing-" + uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".preparing-", dir=root))
    final_directory = root / bundle_id
    files: dict[str, str] = {}
    hashes: dict[str, str] = {}
    try:
        sources = {"summary.md": summary, "image.png": image, "podcast-message.txt": podcast_message, "audio.mp3": audio}
        for filename, source in sources.items():
            required = (filename == "summary.md" and send_summary) or (filename == "image.png" and send_image) or (filename in {"podcast-message.txt", "audio.mp3"} and send_podcast)
            if not source.is_file() or source.stat().st_size == 0:
                if required:
                    raise FileNotFoundError(f"Generated briefing file is missing or empty: {source}")
                continue
            shutil.copy2(source, temporary / filename)
            hashes[filename] = file_digest(temporary / filename)
            files[filename] = filename

        steps: list[dict[str, Any]] = []

        def add_step(step_id: str, stage: str, filename: str) -> None:
            steps.append({"id": step_id, "stage": stage, "file": filename, "status": "pending", "attempts": 0})

        if send_image:
            add_step("image", "image", "image.png")
        if send_summary:
            message = (temporary / "summary.md").read_text(encoding="utf-8-sig").strip()
            chunks = split_message(message, 2980)
            if not chunks:
                raise ValueError("The briefing summary is empty.")
            for index, chunk in enumerate(chunks, start=1):
                filename = f"summary-{index:02}.txt"
                body = chunk if len(chunks) == 1 else f"({index}/{len(chunks)})\n{chunk}"
                (temporary / filename).write_text(body, encoding="utf-8")
                hashes[filename] = file_digest(temporary / filename)
                add_step(f"summary-{index:02}", "summary", filename)
        if send_summary and send_podcast:
            add_step("notice", "notice", "summary.md")
        if send_podcast:
            add_step("podcast", "podcast", "podcast-message.txt")

        state = {
            "schema_version": 1, "bundle_id": bundle_id,
            "episode_id": episode_id, "date_label": date_label, "agency": agency, "room": room,
            "created_at": utc_now(), "source_run_id": source_run_id,
            "status": "ready" if steps else "completed", "steps": steps, "files": files, "sha256": hashes,
        }
        save_state(temporary / "delivery.json", state)
        temporary.rename(final_directory)
        return state
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def check_bundle_files(path: Path, state: dict[str, Any]) -> None:
    for filename, expected in state["sha256"].items():
        file_path = path.parent / filename
        if file_path.parent != path.parent or not file_path.is_file() or file_digest(file_path) != expected:
            raise RuntimeError(f"Saved briefing file is missing or changed: {filename}")


def status_catalog(outbox: Path | None = None) -> dict[str, Any]:
    records = []
    for path in (outbox or outbox_directory()).glob("*/delivery.json"):
        try:
            state = load_state(path)
            if state["status"] == "superseded":
                continue
            remaining = [step for step in state["steps"] if step["status"] != "sent"]
            stages = list(dict.fromkeys(STAGE_LABELS[step["stage"]] for step in remaining))
            valid = True
            try:
                check_bundle_files(path, state)
            except (OSError, RuntimeError):
                valid = False
            records.append({
                "bundle_id": state["bundle_id"], "date_label": state["date_label"],
                "agency": state["agency"], "room": state["room"], "created_at": state["created_at"],
                "status": state["status"], "remaining": stages, "valid": valid,
            })
        except (OSError, ValueError, KeyError, TypeError):
            continue
    records.sort(key=lambda item: item["created_at"], reverse=True)
    return {"pending": [item for item in records if item["status"] != "completed"], "latest_completed": next((item for item in records if item["status"] == "completed"), None)}


def command_for_step(step: dict[str, Any], state: dict[str, Any], directory: Path, project_root: Path, mcp_command: str) -> list[str]:
    scripts = project_root / "scripts"
    common = ["--room", state["room"]]
    if step["stage"] == "image":
        return [sys.executable, str(scripts / "post_kakao_image_attach.py"), *common, "--image", str(directory / step["file"]), "--send-wait", "8"]
    if step["stage"] == "notice":
        return [sys.executable, str(scripts / "publish_kakao_notice.py"), "publish", *common, "--notice-file", str(directory / "summary.md"), "--comment-file", str(directory / "podcast-message.txt")]
    command = [sys.executable, str(scripts / "post_summary_mcp.py"), *common, "--summary", str(directory / step["file"])]
    if mcp_command:
        command.extend(["--mcp-command", mcp_command])
    return command


def last_json_object(output: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", output):
        try:
            value, end = decoder.raw_decode(output, match.start())
            if isinstance(value, dict) and not output[end:].strip():
                return value
        except ValueError:
            continue
    raise RuntimeError("Delivery did not return a readable result.")


def validate_delivery_result(stage: str, result: dict[str, Any]) -> None:
    if result.get("skipped") or result.get("delivery_paused"):
        raise RuntimeError("카카오톡 전송이 중지되어 발송하지 않았습니다.")
    if result.get("error") or result.get("success") is False:
        raise RuntimeError(str(result.get("error") or "카카오톡 전송 실패"))
    if stage == "image":
        success = result.get("dialog_closed") is True
    elif stage == "notice":
        success = result.get("notice_registered") is True and result.get("comment_registered") is True
    else:
        sent = result.get("sent") or []
        success = result.get("chunks", 0) > 0 and len(sent) == result["chunks"] and all(isinstance(item, dict) and not item.get("error") and item.get("success") is not False for item in sent)
    if not success:
        raise RuntimeError("카카오톡 발송 완료를 확인하지 못했습니다.")


def run_delivery_command(command: list[str], directory: Path, step_id: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["QWERTY_KAKAO_LOCK_PARENT"] = str(os.getpid())
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    (directory / f"{step_id}.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"전송 단계 실패: {step_id}. 저장된 전송 로그를 확인해 주세요.")
    return last_json_object(result.stdout)


def supersede_older_bundles(path: Path, state: dict[str, Any]) -> None:
    for other_path in path.parent.parent.glob("*/delivery.json"):
        if other_path == path:
            continue
        try:
            other = load_state(other_path)
            if other["episode_id"] == state["episode_id"] and other["room"] == state["room"] and other["created_at"] < state["created_at"] and other["status"] not in {"completed", "superseded"}:
                other["status"] = "superseded"
                other["superseded_by"] = state["bundle_id"]
                save_state(other_path, other)
        except (OSError, ValueError, KeyError):
            continue


def send_saved_briefing(
    path: Path, project_root: Path, mcp_command: str = "",
    *, sender: Callable[[list[str], Path, str], dict[str, Any]] = run_delivery_command,
) -> dict[str, Any]:
    with kakao_delivery_lock():
        state = load_state(path)
        if state["status"] in {"completed", "superseded"}:
            return state
        check_bundle_files(path, state)
        summary_steps = [step for step in state["steps"] if step["stage"] == "summary"]
        notice_repost_needed = bool(summary_steps) and all(step["status"] == "sent" for step in summary_steps)
        if is_kakao_delivery_paused():
            state["status"] = "paused"
            state["last_error"] = "카카오톡 전송이 꺼져 있습니다."
            save_state(path, state)
            return state
        state["status"] = "sending"
        state["last_attempt_at"] = utc_now()
        save_state(path, state)
        for step in state["steps"]:
            if step["status"] == "sent":
                continue
            if is_kakao_delivery_paused():
                state["status"] = "paused"
                state["last_error"] = "카카오톡 전송이 중지됐습니다."
                save_state(path, state)
                return state
            step["status"] = "sending"
            step["attempts"] += 1
            save_state(path, state)
            if os.getenv("QWERTY_BRIEFING_PROGRESS", "1") != "0":
                print(json.dumps({"phase": "delivery", "step": step["id"]}, ensure_ascii=False), flush=True)
            try:
                if notice_repost_needed and step["stage"] == "notice":
                    # Notice registration targets the latest outgoing bubble.
                    for summary_step in state["steps"]:
                        if summary_step["stage"] == "summary":
                            result = sender(command_for_step(summary_step, state, path.parent, project_root, mcp_command), path.parent, "notice-repost-" + summary_step["id"])
                            validate_delivery_result("summary", result)
                result = sender(command_for_step(step, state, path.parent, project_root, mcp_command), path.parent, step["id"])
                validate_delivery_result(step["stage"], result)
                step["status"] = "sent"
                step["sent_at"] = utc_now()
                step.pop("error", None)
            except Exception as exc:
                step["status"] = "failed"
                step["error"] = str(exc)
                state["status"] = "failed"
                state["last_error"] = str(exc)
                save_state(path, state)
                # The audio link can still be delivered when only notice registration failed.
                if step["stage"] != "notice":
                    return state
            save_state(path, state)
        if all(step["status"] == "sent" for step in state["steps"]):
            state["status"] = "completed"
            state["completed_at"] = utc_now()
            state.pop("last_error", None)
            save_state(path, state)
            supersede_older_bundles(path, state)
        return state
