# 병무청 뉴스 카카오톡 단톡방 자동 게시

매일 KST 기준 전날의 병무청 관련 보도자료/언론기사를 수집해 요약하고, 로그인된 Windows PC 카카오톡 단톡방에 붙여넣어 전송하는 도구입니다.

## 구조

- 뉴스 수집: 기본은 k-skill `naver-news-search` 프록시 기반 네이버 뉴스 검색, 선택적으로 정책브리핑 RSS/Google News RSS/네이버 공식 API 직접 호출
- 요약: self-hosted 실행에서는 로그인된 Codex CLI를 LLM으로 사용, GitHub-hosted 요약 전용 workflow는 `OPENAI_API_KEY`가 있으면 OpenAI API를 쓰고 없으면 제목/요약 기반 간단 요약
- 게시: Windows PC 카카오톡 GUI를 `pyautogui`로 제어
- GitHub Actions: 수집/요약은 가능하지만, 단톡방 게시는 카카오톡이 로그인된 Windows PC 또는 self-hosted runner에서만 가능
- 음성 요약: `edge-tts`로 MP3를 만들고 `podcast/` 정적 플레이어에서 재생

## 설치

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

`.env`에서 최소한 `TARGET_CHATROOM`을 실제 단톡방 이름으로 바꾸세요. 처음에는 `KAKAO_ENABLED=false`를 유지하고 dry run부터 확인하는 편이 좋습니다.

## 테스트 실행

```powershell
.\.venv\Scripts\python -m kakao_mma_news --dry-run
```

특정 날짜를 확인하려면:

```powershell
.\.venv\Scripts\python -m kakao_mma_news --date 2026-05-15 --dry-run
```

결과는 `runs/summary-YYYY-MM-DD.md`와 `runs/articles-YYYY-MM-DD.json`에 저장됩니다.

Codex CLI로 요약을 테스트하려면 Codex 앱/CLI 로그인이 되어 있는 Windows 사용자 세션에서 실행하세요.

```powershell
$env:SUMMARY_PROVIDER="codex"
$env:CODEX_COMMAND="C:\Users\April\AppData\Roaming\npm\codex.cmd"
.\.venv\Scripts\python -m kakao_mma_news --date 2026-05-15 --dry-run
```

## PC 카카오톡 게시

PC 카카오톡이 로그인되어 있고, 잠금 화면이 아니며, 단톡방 이름이 검색 가능해야 합니다.

```powershell
.\.venv\Scripts\python -m kakao_mma_news --post
```

기본 동작은 카카오톡 창 포커스, `Ctrl+F`, 단톡방 이름 붙여넣기, Enter, 메시지 붙여넣기, Enter입니다. 환경에 따라 검색 단축키가 다르면 `.env`의 `KAKAO_SEARCH_HOTKEY`를 수정하거나 `KAKAO_SEARCH_CLICK_X/Y`, `KAKAO_MESSAGE_CLICK_X/Y` 좌표를 지정하세요.

## 음성 요약 플레이어

요약 Markdown을 음성 브리핑 MP3로 변환하려면:

```powershell
.\.venv\Scripts\python scripts\build_podcast_audio.py --date 2026-05-16 --summary runs\summary-2026-05-16.md --site-base-url "https://YOUR_GITHUB_USER.github.io/YOUR_REPO/podcast/" --target-minutes 5
```

생성 결과는 `podcast/audio/YYYY-MM-DD.mp3`, `podcast/scripts/YYYY-MM-DD.txt`, `podcast/manifest.json`에 저장됩니다. 음성 스크립트는 인사말 뒤 기사 제목과 주요 내용 1~2문장만 읽도록 압축하며, 기본 목표 길이는 5분 상한입니다. `podcast/index.html`은 GitHub Pages에서 `manifest.json`을 읽어 최신 음성 요약을 재생합니다.

## 매일 자동 실행

Windows 작업 스케줄러에 등록:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_task.ps1 -Time 08:00
```

GUI 자동화라서 사용자가 로그인된 세션에서 실행되어야 합니다. PC가 잠겨 있거나 절전 상태면 전송이 실패할 수 있습니다.

## GitHub Actions

기본 요약 전용 workflow는 GitHub-hosted Windows runner에서 Markdown 요약 파일만 만듭니다. GitHub-hosted runner에는 사용자의 PC 카카오톡이 없고 Codex CLI 로그인 상태도 유지되지 않으므로, 단톡방 게시나 Codex CLI 기반 요약에는 적합하지 않습니다.

단톡방 게시까지 Actions로 하려면 Windows PC에 GitHub self-hosted runner를 설치하고, PC 카카오톡을 로그인 상태로 켜 둔 뒤 `.github/workflows/daily-post-mcp-self-hosted.yml`을 사용하세요. 이 workflow는 Codex CLI로 네이버 뉴스 요약을 생성하고, `kronenz/kakaotalk-mcp`를 설치한 뒤 `scripts/post_summary_mcp.py`로 `runs/summary-YYYY-MM-DD.md`를 채팅방에 전송합니다.

GitHub 저장소 설정에서 아래 값을 준비하세요.

- Repository variable `TARGET_CHATROOM`: 카카오톡 채팅방 이름
- Repository variable `SUMMARY_PROVIDER`: 선택, self-hosted 게시 workflow 기본값은 `codex`
- Repository variable `CODEX_COMMAND`: 선택, 기본값은 `C:\Users\April\AppData\Roaming\npm\codex.cmd`
- Repository variable `CODEX_TIMEOUT_SECONDS`: 선택, 기본값은 `300`
- Repository variable `PODCAST_BASE_URL`: 선택, 기본값은 `https://<owner>.github.io/<repo>/podcast/`
- Repository variable `PODCAST_LISTEN_URL_TEMPLATE`: 선택, private repo처럼 Pages를 못 쓰면 `https://github.com/<owner>/<repo>/blob/main/podcast/audio/{date}.mp3`
- Repository variable `TTS_VOICE`: 선택, 기본값은 `ko-KR-SunHiNeural`
- Repository variable `TTS_TARGET_MINUTES`: 선택, 기본값은 `5`
- Repository variable `KSKILL_PROXY_BASE_URL`: 선택, 기본값은 `https://k-skill-proxy.nomadamas.org`
- Secret `OPENAI_API_KEY`: 선택, `SUMMARY_PROVIDER=auto` 또는 `openai`일 때 사용합니다.

요약 영구 보관:

- Actions가 만든 `runs/summary-YYYY-MM-DD.md`는 실행 artifact로도 올라가고, 동시에 `summaries/summary-YYYY-MM-DD.md`로 복사되어 GitHub repo에 자동 커밋됩니다.
- 음성 요약은 `podcast/audio/YYYY-MM-DD.mp3`와 `podcast/scripts/YYYY-MM-DD.txt`로 저장되고, `podcast/manifest.json`이 함께 자동 커밋됩니다.
- `runs/` artifact는 실행 기록용 임시 보관이고, `summaries/` 폴더가 장기 보관용 원본입니다.
- 같은 날짜 요약이 이미 있고 내용이 같으면 새 커밋을 만들지 않습니다.

주의: self-hosted runner가 Windows 서비스로 실행되면 GUI 카카오톡 창을 제어하지 못할 수 있습니다. 카카오톡이 열린 사용자 세션에서 runner를 실행해야 합니다.

## 뉴스 수집 설정

기본값은 `NAVER_NEWS_ENABLED=true`, `GOOGLE_NEWS_ENABLED=false`, `POLICY_RSS_ENABLED=false`입니다. 네이버 개발자 센터 키가 없으면 `KSKILL_PROXY_BASE_URL`의 k-skill 프록시를 사용하고, `NAVER_CLIENT_ID`/`NAVER_CLIENT_SECRET`이 있으면 공식 네이버 Open API를 직접 호출합니다.

`NEWS_QUERY_TERMS`는 네이버 뉴스 검색에 넣을 넓은 검색어 목록이고, `NEWS_REQUIRED_TERMS`는 결과에서 반드시 포함되어야 하는 필터입니다. 기본값은 `NEWS_REQUIRED_TERMS=병무청`이라 선거 후보 병역사항처럼 병무청 직접 관련성이 낮은 기사를 줄입니다.

Google News나 정책브리핑 RSS를 보조 출처로 섞고 싶으면 `.env`에서 `GOOGLE_NEWS_ENABLED=true` 또는 `POLICY_RSS_ENABLED=true`로 켜세요.

## 참고 제약

카카오톡 일반 단톡방 직접 게시용 공식 서버 API는 제공되지 않습니다. 이 프로젝트는 PC 카카오톡 GUI 자동화 방식이므로 카카오톡 UI 변경, 로그인 상태, 창 포커스, 보안 정책에 영향을 받습니다.
