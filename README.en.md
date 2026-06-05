# AI MMA Daily Morning Talk

[한국어](./README.md) | [English](./README.en.md) | [Audio Summary Player](https://chlvlftjsrkq.github.io/qwerty/podcast/)

AI MMA Daily Morning Talk is an automated KakaoTalk briefing project for Military Manpower Administration news in Korea. It collects relevant news, creates a concise morning briefing, builds a combined article image sheet, generates an audio summary, and sends the results to a KakaoTalk group chat.

The project is designed for quick morning review before work. It monitors Naver News for the previous business day, plus accumulated weekend or holiday coverage when needed, and organizes news about military service policy, reserve forces, social service personnel, evasion issues, and regional MMA offices.

## Highlights

- Automated news collection and AI briefing for MMA-related topics
- Google Gemini is used first, with Codex used as an auxiliary review tool
- Morning weather summary for Daejeon and Incheon
- Combined article image sheet for visual scanning
- Audio summary MP3 with a GitHub Pages podcast player
- Automated KakaoTalk delivery for briefing text, images, and audio links
- Negative issue monitoring with duplicate issue suppression
- Scheduled briefing operation through an approved automation environment

## Public Outputs

- [Audio summary player](https://chlvlftjsrkq.github.io/qwerty/podcast/)
- Daily briefing Markdown files archived under `summaries/`
- Podcast audio files and playback manifest under `podcast/`

## How It Runs

On a scheduled basis, the automation pipeline collects news, generates AI summaries, builds article image sheets, creates audio summaries, and archives the results. The finished briefing is delivered to the designated KakaoTalk group through an approved delivery environment.

Regular morning briefings can pause on weekends and Korean holidays, then summarize accumulated coverage on the next business day. Negative issue monitoring runs as a separate flow and compares recent alert history with newly collected news to reduce repeated alerts.

## Public Repository Note

This repository is public because GitHub Pages and public briefing artifacts are published from it. A public GitHub repository cannot fully hide its source files. This landing page therefore focuses on the public-facing results and service overview, while secrets and credentials are never stored in the repository.

For stronger source privacy, the automation code should be moved to a private repository, while only public outputs such as `summaries/` and `podcast/` are published through a separate public Pages repository.
