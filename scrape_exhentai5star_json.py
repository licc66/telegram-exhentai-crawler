import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page

# ----------------------------
# 配置
# ----------------------------
CHANNEL_URL = "https://web.telegram.org/k/#@exhentai5star"
USER_DATA_DIR = "./pw_telegram_profile"   # 保存 Telegram Web 登录态
OUT_JSONL = "exhentai5star_records.jsonl"  # JSON Lines，增量写入更方便

# 滚动参数
MAX_NO_NEW_ROUNDS = 60
SCROLL_ROUNDS_LIMIT = 50000
SCROLL_PAUSE_MS = 1200

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def stable_record_id(preview_url: str, hashtags: List[str], rating: Optional[float], fav_count: Optional[int], publish_date_iso: str = "") -> str:
    """优先用预览链接做稳定去重；没有时再退化到其他字段。"""
    if preview_url:
        base = preview_url.strip()
    else:
        base = "|".join(sorted(set(hashtags))) + f"|{rating}|{fav_count}|{publish_date_iso}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()



def load_existing_records(jsonl_path: str) -> List[Dict]:
    path = Path(jsonl_path)
    if not path.exists():
        return []

    records: List[Dict] = []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print(f"[警告] 第 {line_no} 行 JSONL 解析失败，已跳过。")
                continue
            if isinstance(obj, dict):
                records.append(obj)
    return records



def save_records_to_jsonl(jsonl_path: str, records: List[Dict]) -> None:
    path = Path(jsonl_path)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")



def normalize_hashtag(tag_text: str) -> str:
    tag_text = tag_text.strip()
    if tag_text.startswith("#"):
        tag_text = tag_text[1:]
    return tag_text.strip().lower()



def extract_preview_link(soup: BeautifulSoup) -> str:
    for a in soup.select("a.anchor-url"):
        href = (a.get("href") or "").strip()
        text = a.get_text(" ", strip=True)
        if not href:
            continue
        if "telegra.ph" in href:
            return href
        if text.startswith("[") and href.startswith("http"):
            return href

    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if "telegra.ph" in href:
            return href
    return ""



def extract_preview_title(soup: BeautifulSoup, preview_url: str) -> str:
    if preview_url:
        for a in soup.select("a.anchor-url[href]"):
            href = (a.get("href") or "").strip()
            if href == preview_url:
                return a.get_text(" ", strip=True)
    return ""



def parse_rating_and_fav(text: str) -> Dict[str, Optional[float]]:
    rating = None
    fav_count = None

    m_rating = re.search(r"评分\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)", text)
    if m_rating:
        rating = float(m_rating.group(1))

    m_fav = re.search(r"收藏数\s*[:：]\s*(\d[\d,]*)", text)
    if m_fav:
        fav_count = int(m_fav.group(1).replace(",", ""))

    return {"rating": rating, "fav_count": fav_count}



def extract_original_url(soup: BeautifulSoup) -> str:
    for a in soup.select("a.anchor-url[href]"):
        href = (a.get("href") or "").strip()
        if "exhentai.org" in href or "e-hentai.org" in href:
            return href
    return ""



def parse_publish_date(text: str) -> Dict[str, str]:
    text = (text or "").strip()
    if not text:
        return {"publish_date_raw": "", "publish_date_iso": ""}

    m = re.search(r"\b([A-Z][a-z]+)\s+(\d{1,2})\b", text)
    if not m:
        return {"publish_date_raw": text, "publish_date_iso": ""}

    month_name = m.group(1)
    day = int(m.group(2))
    month = MONTHS.get(month_name.lower())
    if not month:
        return {"publish_date_raw": text, "publish_date_iso": ""}

    now = datetime.now()
    year = now.year
    try:
        dt = datetime(year, month, day)
        if dt > now + timedelta(days=30):
            dt = datetime(year - 1, month, day)
    except ValueError:
        return {"publish_date_raw": text, "publish_date_iso": ""}

    return {
        "publish_date_raw": f"{month_name} {day}",
        "publish_date_iso": dt.strftime("%Y-%m-%d"),
    }



def extract_publish_date(message_soup: BeautifulSoup) -> Dict[str, str]:
    candidates = []

    for node in message_soup.select("span.i18n[dir='auto'], time, .time, .message-time"):
        txt = node.get_text(" ", strip=True)
        if txt:
            candidates.append(txt)

    text = message_soup.get_text(" ", strip=True)
    for m in re.finditer(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}\b", text):
        candidates.append(m.group(0))

    seen = set()
    for cand in candidates:
        cand = cand.strip()
        if not cand or cand in seen:
            continue
        seen.add(cand)
        parsed = parse_publish_date(cand)
        if parsed["publish_date_raw"]:
            return parsed

    return {"publish_date_raw": "", "publish_date_iso": ""}



def parse_one_message_outerhtml(message_div_html: str) -> Optional[Dict]:
    soup = BeautifulSoup(message_div_html, "lxml")

    content = soup.select_one("span.translatable-message") or soup.select_one("div.message") or soup
    text = content.get_text("\n", strip=True)
    if not text:
        return None

    hashtags = []
    seen = set()
    for a in content.select("a.anchor-hashtag"):
        tag = normalize_hashtag(a.get_text("", strip=True))
        if tag and tag not in seen:
            hashtags.append(tag)
            seen.add(tag)

    preview_url = extract_preview_link(content)
    preview_title = extract_preview_title(content, preview_url)
    original_url = extract_original_url(content)
    metrics = parse_rating_and_fav(text)
    publish_info = extract_publish_date(soup)

    if not hashtags and not preview_url and metrics["rating"] is None and metrics["fav_count"] is None:
        return None

    record = {
        "hashtags": hashtags,
        "rating": metrics["rating"],
        "fav_count": metrics["fav_count"],
        "preview_url": preview_url,
        "preview_title": preview_title,
        "original_url": original_url,
        "publish_date_raw": publish_info["publish_date_raw"],
        "publish_date_iso": publish_info["publish_date_iso"],
    }
    record["id"] = stable_record_id(
        preview_url=record["preview_url"],
        hashtags=record["hashtags"],
        rating=record["rating"],
        fav_count=record["fav_count"],
        publish_date_iso=record["publish_date_iso"],
    )
    return record


async def ensure_in_channel(page: Page, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)
    await page.wait_for_selector("div.message, div.bubble", timeout=60_000)


async def extract_visible_message_outerhtml(page: Page) -> List[str]:
    js = """
    () => {
      const nodes = Array.from(document.querySelectorAll('div.message.spoilers-container, div.message'));
      const htmls = [];
      for (const n of nodes) {
        const t = (n.innerText || '').trim();
        if (t.length < 2) continue;
        htmls.push(n.outerHTML);
      }
      return htmls;
    }
    """
    return await page.evaluate(js)


async def scroll_messages_container_up(page: Page) -> dict:
    js = """
    () => {
      const msg = document.querySelector('div.message.spoilers-container, div.message, div.bubble');
      if (!msg) return { ok:false, reason:'no_message_node' };

      let el = msg.parentElement;
      while (el) {
        const style = window.getComputedStyle(el);
        const scrollable = (style.overflowY === 'auto' || style.overflowY === 'scroll');
        if (scrollable && el.scrollHeight > el.clientHeight + 200) break;
        el = el.parentElement;
      }
      if (!el) return { ok:false, reason:'no_scroll_parent' };

      const beforeTop = el.scrollTop;
      const beforeH = el.scrollHeight;
      el.scrollBy(0, -Math.floor(el.clientHeight * 0.9));
      const afterTop = el.scrollTop;
      const afterH = el.scrollHeight;

      return { ok:true, beforeTop, afterTop, beforeH, afterH, atTop: afterTop <= 5 };
    }
    """
    return await page.evaluate(js)


async def scrape_channel_to_jsonl() -> None:
    existing_records = load_existing_records(OUT_JSONL)
    existing_by_id: Dict[str, Dict] = {}
    for rec in existing_records:
        rid = rec.get("id")
        if rid:
            existing_by_id[rid] = rec

    seen_ids_this_run: Set[str] = set()
    total_new = 0
    no_new_rounds = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=False,
            viewport={"width": 1400, "height": 900},
        )
        page = await browser.new_page()

        print("[1/3] 打开频道页面（如未登录，请先在弹出的浏览器里手动登录 Telegram Web）")
        await ensure_in_channel(page, CHANNEL_URL)

        print("[2/3] 开始滚动抓取并写入 JSONL…")
        for round_idx in range(SCROLL_ROUNDS_LIMIT):
            html_list = await extract_visible_message_outerhtml(page)

            new_this_round = 0
            for html in html_list:
                record = parse_one_message_outerhtml(html)
                if not record:
                    continue

                rid = record["id"]
                if rid in seen_ids_this_run:
                    continue
                seen_ids_this_run.add(rid)

                if rid not in existing_by_id:
                    existing_by_id[rid] = record
                    new_this_round += 1
                else:
                    merged = existing_by_id[rid]
                    for key, value in record.items():
                        if key == "id":
                            continue
                        if merged.get(key) in (None, "", []) and value not in (None, "", []):
                            merged[key] = value

            if new_this_round > 0:
                save_records_to_jsonl(OUT_JSONL, list(existing_by_id.values()))

            total_new += new_this_round
            print(
                f"round={round_idx:04d} visible={len(html_list)} new={new_this_round} "
                f"total_new={total_new} total_saved={len(existing_by_id)}"
            )

            if new_this_round == 0:
                no_new_rounds += 1
            else:
                no_new_rounds = 0

            if no_new_rounds >= MAX_NO_NEW_ROUNDS:
                print("[3/3] 连续多轮无新增，推测已到最早消息，停止。")
                break

            await scroll_messages_container_up(page)
            await page.wait_for_timeout(SCROLL_PAUSE_MS)

        await browser.close()

    save_records_to_jsonl(OUT_JSONL, list(existing_by_id.values()))
    print("完成：输出文件 ->", OUT_JSONL)


if __name__ == "__main__":
    asyncio.run(scrape_channel_to_jsonl())
