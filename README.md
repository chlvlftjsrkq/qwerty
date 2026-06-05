# AI 병무청 데일리 모닝톡

[한국어](#한국어) | [English](#English) | [음성요약 플레이어](https://chlvlftjsrkq.github.io/qwerty/podcast/)

<a id="한국어"></a>
## 한국어

AI 병무청 데일리 모닝톡은 병무청 관련 뉴스를 매일 수집해 핵심 브리핑, 기사 이미지 모음, 음성요약 링크를 카카오톡 단체방으로 자동 발송하는 프로젝트입니다.

이 프로젝트는 출근 전 짧은 시간에 병무청 관련 주요 이슈를 빠르게 확인할 수 있도록 만들어졌습니다. 네이버 뉴스 기준으로 전날 기사와 주말·공휴일 누적 기사를 확인하고, 병역 제도, 예비군, 사회복무요원, 병역기피 이슈, 지방병무청 소식 등을 중요도와 중복 여부를 고려해 정리합니다.

### 주요 기능

- 병무청 관련 뉴스 자동 수집 및 AI 요약
- Google Gemini를 우선 활용하고, Codex는 보조 검토 도구로 함께 활용
- 대전과 인천 날씨를 포함한 아침 브리핑 생성
- 기사 대표 이미지를 모은 브리핑 이미지 생성
- 음성요약 MP3와 GitHub Pages 기반 재생 플레이어 제공
- 카카오톡 단체방으로 브리핑, 이미지, 음성요약 링크 자동 발송
- 부정 이슈 탐지와 중복 이슈 판단을 통해 반복 알림 최소화
- GitHub Actions와 Windows 작업 스케줄러를 함께 사용하는 완전 자동화 운영

### 공개 결과물

- [음성요약 플레이어](https://chlvlftjsrkq.github.io/qwerty/podcast/)
- `summaries/`에 보관되는 일자별 브리핑 Markdown
- `podcast/`에 보관되는 음성요약 파일과 재생용 manifest

### 운영 방식

아침 브리핑은 카카오톡이 로그인된 Windows PC에서 GitHub Actions workflow를 호출하는 방식으로 실행됩니다. GitHub Actions는 뉴스 수집, AI 요약, 이미지 생성, 음성요약 생성, 결과 보관을 수행하고, self-hosted Windows runner가 PC 카카오톡을 통해 메시지를 전송합니다.

주말과 공휴일에는 정규 아침 브리핑을 쉬고, 다음 영업일에 누적 기사를 종합해 보낼 수 있도록 구성되어 있습니다. 병무청 부정 이슈 모니터링은 별도 workflow로 운영하며, 실제 알림 이력과 최근 기사 내용을 비교해 중복 알림을 줄입니다.

### 공개 저장소 안내

이 저장소는 GitHub Pages와 요약 결과물을 공개하기 위해 공개 상태로 운영됩니다. 공개 저장소 특성상 저장소 안의 소스 파일 자체를 완전히 숨길 수는 없습니다. 다만 첫 화면은 결과물과 서비스 설명 중심으로 정리했고, API 키와 인증 정보는 저장소에 포함하지 않습니다.

소스코드 비공개가 꼭 필요하면 자동화 코드는 private 저장소로 분리하고, `summaries/`와 `podcast/` 같은 공개 결과물만 별도 public Pages 저장소로 배포하는 구조가 가장 안전합니다.

<a id="English"></a>
## English

AI MMA Daily Morning Talk is an automated KakaoTalk briefing project for Military Manpower Administration news in Korea. It collects relevant news, creates a concise morning briefing, builds a combined article image sheet, generates an audio summary, and sends the results to a KakaoTalk group chat.

The project is designed for quick morning review before work. It monitors Naver News for the previous business day, plus accumulated weekend or holiday coverage when needed, and organizes news about military service policy, reserve forces, social service personnel, evasion issues, and regional MMA offices.

### Highlights

- Automated news collection and AI briefing for MMA-related topics
- Google Gemini is used first, with Codex used as an auxiliary review tool
- Morning weather summary for Daejeon and Incheon
- Combined article image sheet for visual scanning
- Audio summary MP3 with a GitHub Pages podcast player
- Automated KakaoTalk delivery for briefing text, images, and audio links
- Negative issue monitoring with duplicate issue suppression
- Fully automated operation using GitHub Actions and Windows Task Scheduler

### Public Outputs

- [Audio summary player](https://chlvlftjsrkq.github.io/qwerty/podcast/)
- Daily briefing Markdown files archived under `summaries/`
- Podcast audio files and playback manifest under `podcast/`

### How It Runs

The morning briefing is triggered from a Windows PC where KakaoTalk is already logged in. The PC dispatches a GitHub Actions workflow, and the self-hosted Windows runner handles news collection, AI summarization, image generation, audio generation, archival, and KakaoTalk delivery.

Regular morning briefings can pause on weekends and Korean holidays, then summarize accumulated coverage on the next business day. Negative issue monitoring runs as a separate workflow and compares recent alert history with newly collected news to reduce repeated alerts.

### Public Repository Note

This repository is public because GitHub Pages and public briefing artifacts are published from it. A public GitHub repository cannot fully hide its source files. This landing page therefore focuses on the public-facing results and service overview, while secrets and credentials are never stored in the repository.

For stronger source privacy, the automation code should be moved to a private repository, while only public outputs such as `summaries/` and `podcast/` are published through a separate public Pages repository.
