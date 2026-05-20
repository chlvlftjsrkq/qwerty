from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


USER_AGENT = "agency-news-talkbriefing/0.1 (+image-sheet)"
BACKGROUND = (255, 255, 255)
TEXT = (25, 30, 36)
MUTED = (88, 97, 110)
BORDER = (218, 224, 232)
CARD = (248, 250, 252)
ACCENT = (38, 91, 166)


@dataclass
class ImageRecord:
    index: int
    title: str
    source: str
    url: str
    image_url: str
    image_path: str
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a Kakao-friendly contact sheet from article images.")
    parser.add_argument("--articles", required=True, help="Path to runs/articles-YYYY-MM-DD.json")
    parser.add_argument("--output", required=True, help="PNG path to write")
    parser.add_argument("--date", required=True, help="Target date label")
    parser.add_argument("--agency", required=True, help="Agency or keyword label")
    parser.add_argument("--title", default="", help="Optional custom title for the image sheet")
    parser.add_argument("--limit", type=int, default=10, help="Maximum articles/images")
    parser.add_argument("--timeout", type=float, default=12.0, help="HTTP timeout in seconds")
    return parser.parse_args()


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\malgunbd.ttf" if bold else r"C:\Windows\Fonts\malgun.ttf",
        r"C:\Windows\Fonts\NanumGothicBold.ttf" if bold else r"C:\Windows\Fonts\NanumGothic.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    if not text:
        return 0
    box = draw.textbbox((0, 0), text, font=font)
    return int(box[2] - box[0])


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    text = normalize_space(text)
    if not text:
        return []
    lines: list[str] = []
    current = ""
    tokens = re.findall(r"\S+\s*", text)
    for token in tokens:
        candidate = current + token
        if text_width(draw, candidate.rstrip(), font) <= max_width:
            current = candidate
            continue
        if current.strip():
            lines.append(current.strip())
            current = ""
        chunk = ""
        for char in token.strip():
            if text_width(draw, chunk + char, font) <= max_width:
                chunk += char
            else:
                if chunk:
                    lines.append(chunk)
                chunk = char
        current = chunk + (" " if token.endswith(" ") else "")
    if current.strip():
        lines.append(current.strip())
    return lines


def image_meta_url(page_url: str, timeout: float) -> str:
    if not page_url:
        return ""
    response = requests.get(
        page_url,
        timeout=timeout,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    selectors = [
        ("property", "og:image"),
        ("property", "og:image:url"),
        ("name", "twitter:image"),
        ("name", "image"),
    ]
    for key, value in selectors:
        tag = soup.find("meta", attrs={key: value})
        content = normalize_space(tag.get("content", "")) if tag else ""
        if content:
            return urljoin(page_url, content)

    for img in soup.find_all("img", src=True):
        src = normalize_space(img.get("src", ""))
        if not src:
            continue
        width = int(img.get("width") or 0) if str(img.get("width") or "").isdigit() else 0
        height = int(img.get("height") or 0) if str(img.get("height") or "").isdigit() else 0
        if width and height and (width < 160 or height < 90):
            continue
        return urljoin(page_url, src)
    return ""


def download_image(image_url: str, output_path: Path, timeout: float) -> str:
    response = requests.get(
        image_url,
        timeout=timeout,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": f"{urlparse(image_url).scheme}://{urlparse(image_url).netloc}/",
        },
    )
    response.raise_for_status()
    image = Image.open(BytesIO(response.content))
    image = ImageOps.exif_transpose(image).convert("RGB")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, quality=92)
    return str(output_path)


def placeholder_image(index: int, title: str, size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, (232, 238, 246))
    draw = ImageDraw.Draw(image)
    title_font = load_font(32, bold=True)
    body_font = load_font(24)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=20, fill=(255, 255, 255), outline=BORDER, width=2)
    draw.text((42, 42), f"{index:02d}", fill=ACCENT, font=load_font(42, bold=True))
    lines = wrap_text(draw, title, title_font, width - 84)[:3]
    y = 118
    for line in lines:
        draw.text((42, y), line, fill=TEXT, font=title_font)
        y += 42
    draw.text((42, height - 60), "대표 이미지를 찾지 못해 제목 카드로 대체했습니다.", fill=MUTED, font=body_font)
    return image


def fit_image_without_crop(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    source = image.convert("RGB")
    try:
        background = ImageOps.fit(source, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        background = background.filter(ImageFilter.GaussianBlur(20))
        wash = Image.new("RGB", size, (255, 255, 255))
        canvas = Image.blend(background, wash, 0.62)
    except Exception:
        canvas = Image.new("RGB", size, CARD)

    foreground = ImageOps.contain(source, size, method=Image.Resampling.LANCZOS)
    x = (size[0] - foreground.size[0]) // 2
    y = (size[1] - foreground.size[1]) // 2
    canvas.paste(foreground, (x, y))
    return canvas


def draw_rounded_image(canvas: Image.Image, image: Image.Image, box: tuple[int, int, int, int], radius: int = 18) -> None:
    x1, y1, x2, y2 = box
    tile = image.resize((x2 - x1, y2 - y1), Image.Resampling.LANCZOS)
    mask = Image.new("L", tile.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, tile.size[0], tile.size[1]), radius=radius, fill=255)
    canvas.paste(tile, (x1, y1), mask)


def collect_records(articles: list[dict[str, Any]], asset_dir: Path, limit: int, timeout: float) -> list[ImageRecord]:
    records: list[ImageRecord] = []
    for index, article in enumerate(articles[:limit], start=1):
        title = normalize_space(str(article.get("title") or f"기사 {index}"))
        source = normalize_space(str(article.get("source") or "네이버 뉴스"))
        url = normalize_space(str(article.get("url") or ""))
        record = ImageRecord(index=index, title=title, source=source, url=url, image_url="", image_path="")
        try:
            record.image_url = image_meta_url(url, timeout)
            if record.image_url:
                suffix = Path(urlparse(record.image_url).path).suffix.lower()
                if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                    suffix = ".jpg"
                record.image_path = download_image(record.image_url, asset_dir / f"rep-{index:02d}{suffix}", timeout)
            else:
                record.error = "no image meta found"
        except Exception as exc:
            record.error = str(exc)
        records.append(record)
    return records


def build_sheet(records: list[ImageRecord], output_path: Path, date_label: str, agency: str, title: str = "") -> None:
    width = 1500
    margin = 54
    gap = 30
    columns = 2 if len(records) > 1 else 1
    card_width = (width - margin * 2 - gap * (columns - 1)) // columns
    image_height = 310
    inner = 22
    title_font = load_font(31, bold=True)
    source_font = load_font(22)
    header_font = load_font(48, bold=True)

    measure = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    card_heights: list[int] = []
    for record in records:
        title_lines = wrap_text(measure, record.title, title_font, card_width - inner * 2)
        title_block = max(42, len(title_lines) * 38)
        card_heights.append(inner + image_height + 18 + title_block + 34 + inner)

    row_heights: list[int] = []
    for row in range((len(records) + columns - 1) // columns):
        row_heights.append(max(card_heights[row * columns : row * columns + columns]))
    header_title = normalize_space(title) or f"{date_label} {agency} 뉴스 이미지 모음"
    header_lines = wrap_text(measure, header_title, header_font, width - margin * 2)
    header_height = 44 + max(1, len(header_lines)) * 58 + 34
    height = header_height + sum(row_heights) + gap * max(0, len(row_heights) - 1) + margin

    canvas = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    header_y = 36
    for line in header_lines:
        draw.text((margin, header_y), line, fill=TEXT, font=header_font)
        header_y += 58
    draw.line((margin, header_height - 24, width - margin, header_height - 24), fill=BORDER, width=2)

    y = header_height
    for row, row_height in enumerate(row_heights):
        for col in range(columns):
            index = row * columns + col
            if index >= len(records):
                continue
            record = records[index]
            x = margin + col * (card_width + gap)
            card_h = card_heights[index]
            draw.rounded_rectangle((x, y, x + card_width, y + card_h), radius=18, fill=CARD, outline=BORDER, width=2)

            image_box = (x + inner, y + inner, x + card_width - inner, y + inner + image_height)
            if record.image_path and Path(record.image_path).exists():
                try:
                    src = Image.open(record.image_path)
                    img = fit_image_without_crop(src, (image_box[2] - image_box[0], image_box[3] - image_box[1]))
                except Exception:
                    img = placeholder_image(record.index, record.title, (image_box[2] - image_box[0], image_box[3] - image_box[1]))
            else:
                img = placeholder_image(record.index, record.title, (image_box[2] - image_box[0], image_box[3] - image_box[1]))
            draw_rounded_image(canvas, img, image_box)

            badge = f"{record.index}"
            badge_box = (image_box[0] + 14, image_box[1] + 14, image_box[0] + 62, image_box[1] + 62)
            draw.rounded_rectangle(badge_box, radius=24, fill=(255, 255, 255), outline=BORDER, width=1)
            bw = text_width(draw, badge, source_font)
            draw.text((badge_box[0] + (48 - bw) / 2, badge_box[1] + 10), badge, fill=ACCENT, font=source_font)

            text_y = image_box[3] + 18
            for line in wrap_text(draw, record.title, title_font, card_width - inner * 2):
                draw.text((x + inner, text_y), line, fill=TEXT, font=title_font)
                text_y += 38
            source = record.source or urlparse(record.url).netloc or "네이버 뉴스"
            draw.text((x + inner, y + card_h - inner - 26), source, fill=MUTED, font=source_font)
        y += row_height + gap

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    articles_path = Path(args.articles)
    output_path = Path(args.output)
    articles = json.loads(articles_path.read_text(encoding="utf-8"))
    if not isinstance(articles, list):
        raise RuntimeError(f"Article JSON must contain a list: {articles_path}")

    asset_dir = output_path.parent / f"{output_path.stem}-assets"
    selected = articles[: max(1, args.limit)]
    if not selected:
        selected = [
            {
                "title": f"{args.agency} 관련 주요 뉴스가 확인되지 않았습니다.",
                "source": "네이버 뉴스",
                "url": "",
            }
        ]

    records = collect_records(selected, asset_dir, args.limit, args.timeout)
    build_sheet(records, output_path, args.date, args.agency, args.title)
    report_path = output_path.with_suffix(".json")
    report_path.write_text(json.dumps([record.__dict__ for record in records], ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "records": len(records), "report": str(report_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
