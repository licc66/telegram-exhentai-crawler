import argparse
import html
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

DATA_FILE = "exhentai5star_records.jsonl"
DEFAULT_HTML_FILE = "search_results.html"
DEFAULT_EXPORT_LIMIT = 500000
PAGE_SIZE = 50

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

WINDOW_SIZE_MAP = {
    "week": 100,
    "month": 400,
}


def normalize_tag(tag: str) -> str:
    tag = str(tag or "").strip().lower()
    if tag.startswith("#"):
        tag = tag[1:]
    return tag



def safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default



def safe_int(v, default=0) -> int:
    try:
        if isinstance(v, str):
            v = v.replace(",", "").strip()
        return int(float(v))
    except Exception:
        return default



def normalize_date_fields(rec: Dict) -> Dict:
    raw = str(rec.get("publish_date_raw") or "").strip()
    iso = str(rec.get("publish_date_iso") or "").strip()

    if iso:
        try:
            datetime.strptime(iso, "%Y-%m-%d")
            rec["publish_date_iso"] = iso
            if not raw:
                rec["publish_date_raw"] = iso
            return rec
        except ValueError:
            rec["publish_date_iso"] = ""

    if raw:
        m = re.search(r"\b([A-Z][a-z]+)\s+(\d{1,2})\b", raw)
        if m:
            month_name = m.group(1)
            day = int(m.group(2))
            month = MONTHS.get(month_name.lower())
            if month:
                now = datetime.now()
                year = now.year
                try:
                    dt = datetime(year, month, day)
                    if dt > now + timedelta(days=30):
                        dt = datetime(year - 1, month, day)
                    rec["publish_date_iso"] = dt.strftime("%Y-%m-%d")
                    rec["publish_date_raw"] = f"{month_name} {day}"
                except ValueError:
                    rec["publish_date_iso"] = ""
    return rec



def cleanup_record(obj: Dict) -> Dict:
    rec = dict(obj)
    rec.setdefault("hashtags", [])
    rec.setdefault("rating", 0)
    rec.setdefault("fav_count", 0)
    rec.setdefault("preview_url", "")
    rec.setdefault("preview_title", "")
    rec.setdefault("original_url", "")
    rec.setdefault("publish_date_raw", "")
    rec.setdefault("publish_date_iso", "")
    rec["hashtags"] = [normalize_tag(t) for t in rec.get("hashtags", []) if normalize_tag(t)]
    rec["rating"] = safe_float(rec.get("rating"), 0)
    rec["fav_count"] = safe_int(rec.get("fav_count"), 0)
    rec["preview_url"] = str(rec.get("preview_url") or "").strip()
    rec["preview_title"] = str(rec.get("preview_title") or "").strip()
    rec["original_url"] = str(rec.get("original_url") or "").strip()
    rec = normalize_date_fields(rec)
    return rec



def deduplicate_records(records: List[Dict]) -> List[Dict]:
    seen = set()
    unique = []
    for rec in records:
        key = (
            rec.get("preview_url", ""),
            rec.get("original_url", ""),
            rec.get("preview_title", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(rec)
    return unique



def load_json_lines_text(text: str) -> Tuple[List[Dict], int]:
    records: List[Dict] = []
    bad_lines = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not (line.startswith("{") and line.endswith("}")):
            bad_lines += 1
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                records.append(cleanup_record(obj))
            else:
                bad_lines += 1
        except json.JSONDecodeError:
            bad_lines += 1
    return deduplicate_records(records), bad_lines



def load_json_array_text(text: str) -> List[Dict]:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, list):
        return []
    return deduplicate_records([cleanup_record(x) for x in obj if isinstance(x, dict)])



def extract_anchor_text(block: str, label: str) -> str:
    m = re.search(
        rf"{re.escape(label)}\s*:<a[^>]*class=\"anchor-url\"[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>",
        block,
        flags=re.I | re.S,
    )
    if not m:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()



def extract_anchor_href(block: str, label: str) -> str:
    m = re.search(
        rf"{re.escape(label)}\s*:<a[^>]*class=\"anchor-url\"[^>]*href=\"([^\"]+)\"",
        block,
        flags=re.I | re.S,
    )
    return html.unescape(m.group(1)).strip() if m else ""



def parse_html_block(block: str) -> Dict:
    hashtags = re.findall(r">#([^<\s]+)<", block, flags=re.I)
    preview_url = extract_anchor_href(block, "预览")
    preview_title = extract_anchor_text(block, "预览")
    original_url = extract_anchor_href(block, "原始地址")

    rating_m = re.search(r"评分\s*:\s*([0-9]+(?:\.[0-9]+)?)", block)
    fav_m = re.search(r"收藏数\s*:\s*([0-9,]+)", block)
    date_m = re.search(r">\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun,\s+)?([A-Z][a-z]+\s+\d{1,2})\s*<", block)

    rec = {
        "hashtags": hashtags,
        "rating": safe_float(rating_m.group(1), 0) if rating_m else 0,
        "fav_count": safe_int(fav_m.group(1), 0) if fav_m else 0,
        "preview_url": preview_url,
        "preview_title": preview_title,
        "original_url": original_url,
        "publish_date_raw": date_m.group(1) if date_m else "",
    }
    return cleanup_record(rec)



def load_raw_html_text(text: str) -> List[Dict]:
    text = text.strip()
    if not text:
        return []

    blocks = re.findall(r'<div class="message spoilers-container".*?</div>\s*$', text, flags=re.I | re.S | re.M)
    if not blocks:
        blocks = re.split(r"\n\s*\n+", text)

    records: List[Dict] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        rec = parse_html_block(block)
        if rec.get("preview_url") or rec.get("preview_title") or rec.get("original_url"):
            records.append(rec)
    return deduplicate_records(records)



def load_records(path_str: str) -> List[Dict]:
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"数据文件不存在: {path_str}")

    text = path.read_text(encoding="utf-8", errors="ignore")

    jsonl_records, bad_lines = load_json_lines_text(text)
    if jsonl_records and len(jsonl_records) >= max(1, len([x for x in text.splitlines() if x.strip()]) // 3):
        print(f"[读取] 识别为 JSONL，共载入 {len(jsonl_records)} 条记录。")
        if bad_lines:
            print(f"[提示] 有 {bad_lines} 行无法按 JSON 解析，已自动跳过。")
        return jsonl_records

    json_records = load_json_array_text(text)
    if json_records:
        print(f"[读取] 识别为 JSON 数组，共载入 {len(json_records)} 条记录。")
        return json_records

    html_records = load_raw_html_text(text)
    if html_records:
        print(f"[读取] 识别为原始 HTML 文本，共解析 {len(html_records)} 条记录。")
        return html_records

    if jsonl_records:
        print(f"[读取] 仅解析到部分 JSONL 记录，共 {len(jsonl_records)} 条。")
        return jsonl_records

    return []



def filter_records(records: List[Dict], query_tags: List[str], mode: str) -> List[Dict]:
    query = [normalize_tag(t) for t in query_tags if normalize_tag(t)]
    if not query:
        return list(records)

    matched = []
    for rec in records:
        tags = {normalize_tag(t) for t in rec.get("hashtags", [])}
        ok = all(t in tags for t in query) if mode == "all" else any(t in tags for t in query)
        if ok:
            matched.append(rec)
    return matched



def get_period_records(records: List[Dict], period: str, window_index: int = 0) -> List[Dict]:
    if period == "all":
        return list(records)
    window_size = WINDOW_SIZE_MAP.get(period)
    if not window_size:
        return list(records)
    start = max(0, window_index) * window_size
    end = start + window_size
    return list(records[start:end])



def get_display_records(records: List[Dict], query_tags: List[str], mode: str, period: str = "all", window_index: int = 0) -> List[Dict]:
    period_records = get_period_records(records, period, window_index)
    return filter_records(period_records, query_tags, mode)



def sort_records(records: List[Dict], sort_by: str) -> List[Dict]:
    if sort_by == "rating":
        key_fn = lambda r: (safe_float(r.get("rating"), 0), safe_int(r.get("fav_count"), 0))
    else:
        key_fn = lambda r: (safe_int(r.get("fav_count"), 0), safe_float(r.get("rating"), 0))
    return sorted(records, key=key_fn, reverse=True)



def print_records(records: List[Dict]) -> None:
    if not records:
        print("没有找到匹配结果。")
        return

    for idx, rec in enumerate(records, start=1):
        hashtags = " ".join("#" + t for t in rec.get("hashtags", []))
        print(f"[{idx}] {rec.get('preview_title') or '(无标题)'}")
        print(f"  hashtags : {hashtags}")
        print(f"  发布时间  : {rec.get('publish_date_raw') or rec.get('publish_date_iso') or '未知'}")
        print(f"  评分      : {rec.get('rating')}")
        print(f"  收藏数    : {rec.get('fav_count')}")
        print(f"  预览链接  : {rec.get('preview_url')}")
        if rec.get("original_url"):
            print(f"  原始地址  : {rec.get('original_url')}")
        print()


HTML_TEMPLATE = """<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>搜索结果 - __QUERY_TEXT__</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; background: #f6f7fb; color: #222; }
    .wrap { max-width: 1220px; margin: 0 auto; padding: 24px; }
    .header { background: #fff; border-radius: 16px; padding: 20px 24px; box-shadow: 0 6px 24px rgba(0,0,0,.06); margin-bottom: 20px; }
    .summary { color: #555; margin-top: 8px; }
    .toolbar { display:flex; flex-wrap:wrap; gap:12px; align-items:flex-end; margin-top:16px; }
    .group { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
    .search-panel { margin-top: 16px; padding: 14px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 14px; }
    .search-row { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
    .search-input { flex: 1 1 420px; min-width: 260px; border: 1px solid #cbd5e1; background:#fff; color:#1e293b; padding:12px 14px; border-radius:10px; font-size:14px; }
    .label { font-size:14px; color:#555; margin-right:4px; }
    select { border: 1px solid #cbd5e1; background:#fff; color:#1e293b; padding:10px 12px; border-radius:10px; font-size:14px; }
    button { border:0; background:#e2e8f0; color:#1e293b; padding:10px 14px; border-radius:10px; cursor:pointer; font-size:14px; }
    button.active { background:#2563eb; color:#fff; }
    button.primary { background:#2563eb; color:#fff; }
    button:disabled { opacity:.45; cursor:not-allowed; }
    .tip { margin-top: 10px; font-size: 13px; color: #64748b; }
    .current-query { margin-top: 10px; font-size: 14px; color: #334155; }
    .chip-list { display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
    .chip { display:inline-flex; align-items:center; border-radius:999px; padding:6px 10px; background:#dbeafe; color:#1d4ed8; font-size:13px; }
    .stats { display:flex; gap:16px; flex-wrap:wrap; margin-top:14px; font-size:14px; color:#334155; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }
    .card { background: #fff; border-radius: 16px; padding: 18px; box-shadow: 0 6px 24px rgba(0,0,0,.06); position: relative; }
    .index { position: absolute; right: 14px; top: 12px; color: #888; font-size: 13px; }
    h2 { font-size: 18px; line-height: 1.45; margin: 0 28px 12px 0; }
    .meta { display: flex; gap: 16px; flex-wrap: wrap; font-size: 14px; margin-bottom: 10px; }
    .tags { display:flex; gap:8px; flex-wrap:wrap; font-size: 14px; color: #444; background: #f1f3f9; border-radius: 10px; padding: 10px 12px; line-height: 1.7; word-break: break-word; }
    .tag-btn { border: 1px solid #cbd5e1; background:#fff; color:#334155; padding:6px 10px; border-radius:999px; font-size:13px; cursor:pointer; }
    .tag-btn:hover { background:#eff6ff; color:#1d4ed8; border-color:#93c5fd; }
    .links { margin-top: 14px; display: flex; gap: 10px; flex-wrap: wrap; }
    .btn { text-decoration: none; background: #2563eb; color: #fff; padding: 10px 14px; border-radius: 10px; font-size: 14px; }
    .btn-secondary { background: #475569; }
    .raw { margin-top: 12px; font-size: 12px; color: #666; word-break: break-all; line-height: 1.6; }
    .muted { color:#64748b; }
    .pager { display:flex; justify-content:center; gap:8px; flex-wrap:wrap; margin: 22px 0 10px; }
    .pager button { min-width: 42px; }
    .footer-note { text-align:center; color:#64748b; font-size:13px; margin-bottom:16px; }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <section class=\"header\">
      <h1>搜索结果</h1>
      <div class=\"summary\">初始查询标签：__QUERY_TEXT__ ｜ 初始匹配模式：__QUERY_MODE__ ｜ 已导入最多 __EXPORT_LIMIT__ 条全部记录 ｜ 每页 __PAGE_SIZE__ 条</div>

      <div class=\"search-panel\">
        <div class=\"search-row\">
          <span class=\"label\">标签搜索</span>
          <input id=\"tagSearchInput\" class=\"search-input\" type=\"text\" placeholder=\"输入一个或多个 tag，用空格分隔，例如：sakuram chinese translation\">
          <span class=\"label\">匹配模式</span>
          <select id=\"queryModeSelect\">
            <option value=\"any\">任一匹配</option>
            <option value=\"all\">全部匹配</option>
          </select>
          <button id=\"searchBtn\" class=\"primary\">搜索</button>
          <button id=\"clearSearchBtn\">清空</button>
        </div>
        <div class=\"tip\">点击下方任意 tag，会自动加入搜索框并再次筛选。</div>
        <div class=\"current-query\">当前标签：<span id=\"currentQueryText\">全部记录</span></div>
        <div class=\"chip-list\" id=\"currentQueryChips\"></div>
      </div>

      <div class=\"toolbar\">
        <div class=\"group\">
          <span class=\"label\">排序字段</span>
          <button class=\"sort-btn active\" data-sort=\"fav\">按收藏数</button>
          <button class=\"sort-btn\" data-sort=\"rating\">按评分</button>
        </div>
        <div class=\"group\">
          <span class=\"label\">时间范围</span>
          <button class=\"period-btn active\" data-period=\"all\">累计</button>
          <button class=\"period-btn\" data-period=\"month\">最近400条</button>
          <button class=\"period-btn\" data-period=\"week\">最近100条</button>
        </div>
        <div class=\"group\" id=\"monthWindowGroup\" style=\"display:none\">
          <span class=\"label\">400条窗口</span>
          <select id=\"monthWindowSelect\"></select>
        </div>
        <div class=\"group\" id=\"weekWindowGroup\" style=\"display:none\">
          <span class=\"label\">100条窗口</span>
          <select id=\"weekWindowSelect\"></select>
        </div>
      </div>
      <div class=\"stats\">
        <span>当前记录数：<strong id=\"statsCount\">0</strong></span>
        <span>当前页：<strong id=\"statsPage\">1 / 1</strong></span>
        <span>累计总评分：<strong id=\"statsRating\">0</strong></span>
        <span>累计总收藏数：<strong id=\"statsFav\">0</strong></span>
      </div>
    </section>
    <section class=\"grid\" id=\"grid\"></section>
    <div class=\"pager\" id=\"pager\"></div>
    <div class=\"footer-note\">“最近100条 / 最近400条” 是先从全部爬取记录切窗口，再按网页当前标签过滤。若全部记录超过导出上限，请调大 <code>--export-limit</code>。</div>
  </div>
  <script>
    const ALL_RECORDS = __PAYLOAD__;
    const PAGE_SIZE = __PAGE_SIZE__;
    const WINDOW_SIZE = { week: 100, month: 400 };
    const INITIAL_QUERY_TAGS = __QUERY_TAGS__;
    const INITIAL_QUERY_MODE = '__QUERY_MODE__';
    let currentSort = 'fav';
    let currentPeriod = 'all';
    let currentPage = 1;
    let currentWeekWindow = 0;
    let currentMonthWindow = 0;
    let currentQueryTags = [...INITIAL_QUERY_TAGS];
    let currentQueryMode = INITIAL_QUERY_MODE || 'any';

    function esc(s) {
      return String(s ?? '').replace(/[&<>\"']/g, function(ch) {
        return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[ch];
      });
    }

    function normalizeTag(tag) {
      tag = String(tag ?? '').trim().toLowerCase();
      return tag.startsWith('#') ? tag.slice(1) : tag;
    }

    function parseTagsFromInput(text) {
      const seen = new Set();
      const out = [];
      String(text || '').split(/\s+/).map(normalizeTag).filter(Boolean).forEach(tag => {
        if (!seen.has(tag)) {
          seen.add(tag);
          out.push(tag);
        }
      });
      return out;
    }

    function syncSearchControls() {
      const input = document.getElementById('tagSearchInput');
      const modeSelect = document.getElementById('queryModeSelect');
      if (input) input.value = currentQueryTags.join(' ');
      if (modeSelect) modeSelect.value = currentQueryMode;
      const text = document.getElementById('currentQueryText');
      const chips = document.getElementById('currentQueryChips');
      if (text) text.textContent = currentQueryTags.length ? currentQueryTags.map(t => '#' + t).join(' ') : '全部记录';
      if (chips) {
        chips.innerHTML = currentQueryTags.length
          ? currentQueryTags.map(t => `<span class=\"chip\">#${esc(t)}</span>`).join('')
          : '';
      }
    }

    function getWindowedItems(period) {
      if (period === 'all') return [...ALL_RECORDS];
      const size = WINDOW_SIZE[period];
      const currentWindow = period === 'week' ? currentWeekWindow : currentMonthWindow;
      const start = currentWindow * size;
      const end = start + size;
      return ALL_RECORDS.slice(start, end);
    }

    function filterItems(items) {
      const query = (currentQueryTags || []).map(normalizeTag).filter(Boolean);
      if (!query.length) return [...items];
      return items.filter(item => {
        const tags = new Set((item.hashtags || []).map(normalizeTag).filter(Boolean));
        return currentQueryMode === 'all'
          ? query.every(t => tags.has(t))
          : query.some(t => tags.has(t));
      });
    }

    function getWindowOptions(period) {
      const size = WINDOW_SIZE[period];
      const total = ALL_RECORDS.length;
      const options = [];
      for (let start = 0; start < total; start += size) {
        const end = Math.min(start + size, total);
        options.push({
          value: String(start / size),
          label: `第 ${start + 1} - ${end} 条`,
        });
      }
      return options;
    }

    function populateWindowSelect(selectId, period, currentValue) {
      const el = document.getElementById(selectId);
      if (!el) return;
      const options = getWindowOptions(period);
      if (!options.length) {
        el.innerHTML = '<option value="0">无可选数据</option>';
        el.value = '0';
        return;
      }
      el.innerHTML = options.map(opt => `<option value="${opt.value}">${esc(opt.label)}</option>`).join('');
      el.value = options.some(opt => opt.value === String(currentValue)) ? String(currentValue) : options[0].value;
    }

    function sortItems(items, sortBy) {
      const arr = [...items];
      arr.sort((a, b) => {
        if (sortBy === 'rating') return (b.rating - a.rating) || (b.fav_count - a.fav_count);
        return (b.fav_count - a.fav_count) || (b.rating - a.rating);
      });
      return arr;
    }

    function updateButtons() {
      document.querySelectorAll('.sort-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.sort === currentSort));
      document.querySelectorAll('.period-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.period === currentPeriod));
      const weekGroup = document.getElementById('weekWindowGroup');
      const monthGroup = document.getElementById('monthWindowGroup');
      if (weekGroup) weekGroup.style.display = currentPeriod === 'week' ? 'flex' : 'none';
      if (monthGroup) monthGroup.style.display = currentPeriod === 'month' ? 'flex' : 'none';
      syncSearchControls();
    }

    function renderPager(totalPages) {
      const pager = document.getElementById('pager');
      if (totalPages <= 1) { pager.innerHTML = ''; return; }
      const btn = (label, page, disabled=false, active=false) => `<button ${disabled ? 'disabled' : ''} class="${active ? 'active' : ''}" data-page="${page}">${label}</button>`;
      let html = '';
      html += btn('上一页', currentPage - 1, currentPage <= 1, false);
      const start = Math.max(1, currentPage - 4);
      const end = Math.min(totalPages, start + 9);
      for (let p = start; p <= end; p++) html += btn(String(p), p, false, p === currentPage);
      html += btn('下一页', currentPage + 1, currentPage >= totalPages, false);
      pager.innerHTML = html;
      pager.querySelectorAll('button[data-page]').forEach(b => b.addEventListener('click', () => {
        const p = Number(b.dataset.page || '1');
        if (!p || p < 1 || p > totalPages || p === currentPage) return;
        currentPage = p;
        render();
        window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
      }));
    }

    function applySearch(resetPage = true) {
      const input = document.getElementById('tagSearchInput');
      const modeSelect = document.getElementById('queryModeSelect');
      currentQueryTags = parseTagsFromInput(input ? input.value : '');
      currentQueryMode = modeSelect ? modeSelect.value : 'any';
      if (resetPage) currentPage = 1;
      render();
    }

    function addTagToSearch(tag) {
      const normalized = normalizeTag(tag);
      if (!normalized) return;
      const merged = [...currentQueryTags];
      if (!merged.includes(normalized)) merged.push(normalized);
      currentQueryTags = merged;
      currentPage = 1;
      syncSearchControls();
      render();
    }

    function bindTagButtons() {
      document.querySelectorAll('.tag-btn[data-tag]').forEach(btn => {
        btn.addEventListener('click', () => addTagToSearch(btn.dataset.tag || ''));
      });
    }

    function render() {
      updateButtons();
      const periodRows = getWindowedItems(currentPeriod);
      const filtered = filterItems(periodRows);
      const rows = sortItems(filtered, currentSort);
      const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
      if (currentPage > totalPages) currentPage = totalPages;
      const start = (currentPage - 1) * PAGE_SIZE;
      const pageRows = rows.slice(start, start + PAGE_SIZE);

      document.getElementById('statsCount').textContent = rows.length;
      document.getElementById('statsPage').textContent = `${currentPage} / ${totalPages}`;
      document.getElementById('statsRating').textContent = rows.reduce((s, x) => s + (Number(x.rating) || 0), 0).toFixed(2);
      document.getElementById('statsFav').textContent = rows.reduce((s, x) => s + (Number(x.fav_count) || 0), 0).toLocaleString();

      const grid = document.getElementById('grid');
      if (!pageRows.length) {
        grid.innerHTML = '<div class="card"><h2>没有匹配结果</h2><div class="muted">当前窗口和查询条件下没有记录。</div></div>';
        renderPager(1);
        return;
      }

      grid.innerHTML = pageRows.map((item, idx) => {
        const realIndex = start + idx + 1;
        const hashtags = (item.hashtags || []).length
          ? (item.hashtags || []).map(t => `<button type="button" class="tag-btn" data-tag="${esc(normalizeTag(t))}">${esc(t.startsWith('#') ? t : '#' + t)}</button>`).join('')
          : '无标签';
        const previewBtn = item.preview_url ? `<a class="btn" href="${esc(item.preview_url)}" target="_blank" rel="noopener noreferrer">打开预览</a>` : '';
        const originalBtn = item.original_url ? `<a class="btn btn-secondary" href="${esc(item.original_url)}" target="_blank" rel="noopener noreferrer">打开原始地址</a>` : '';
        const dateText = item.publish_date_raw || item.publish_date_iso || '未知';
        return `
          <article class="card">
            <div class="index">#${realIndex}</div>
            <h2>${esc(item.title)}</h2>
            <div class="meta">
              <span>发布时间：<strong>${esc(dateText)}</strong></span>
              <span>评分：<strong>${Number(item.rating || 0).toFixed(2)}</strong></span>
              <span>收藏数：<strong>${Number(item.fav_count || 0).toLocaleString()}</strong></span>
            </div>
            <div class="tags">${hashtags}</div>
            <div class="links">${previewBtn}${originalBtn}</div>
            <div class="raw">预览链接：${item.preview_url ? esc(item.preview_url) : '无'}<br>原始地址：${item.original_url ? esc(item.original_url) : '无'}</div>
          </article>`;
      }).join('');

      bindTagButtons();
      renderPager(totalPages);
    }

    document.querySelectorAll('.sort-btn').forEach(btn => btn.addEventListener('click', () => {
      currentSort = btn.dataset.sort;
      currentPage = 1;
      render();
    }));

    document.querySelectorAll('.period-btn').forEach(btn => btn.addEventListener('click', () => {
      currentPeriod = btn.dataset.period;
      currentPage = 1;
      render();
    }));

    populateWindowSelect('weekWindowSelect', 'week', currentWeekWindow);
    populateWindowSelect('monthWindowSelect', 'month', currentMonthWindow);

    const weekWindowSelect = document.getElementById('weekWindowSelect');
    const monthWindowSelect = document.getElementById('monthWindowSelect');
    const searchBtn = document.getElementById('searchBtn');
    const clearSearchBtn = document.getElementById('clearSearchBtn');
    const tagSearchInput = document.getElementById('tagSearchInput');
    const queryModeSelect = document.getElementById('queryModeSelect');

    if (weekWindowSelect) {
      weekWindowSelect.addEventListener('change', (e) => {
        currentWeekWindow = Number(e.target.value || '0');
        currentPage = 1;
        render();
      });
    }

    if (monthWindowSelect) {
      monthWindowSelect.addEventListener('change', (e) => {
        currentMonthWindow = Number(e.target.value || '0');
        currentPage = 1;
        render();
      });
    }

    if (searchBtn) searchBtn.addEventListener('click', () => applySearch(true));
    if (clearSearchBtn) {
      clearSearchBtn.addEventListener('click', () => {
        currentQueryTags = [];
        currentQueryMode = queryModeSelect ? queryModeSelect.value : 'any';
        if (tagSearchInput) tagSearchInput.value = '';
        currentPage = 1;
        render();
      });
    }
    if (tagSearchInput) {
      tagSearchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          applySearch(true);
        }
      });
    }
    if (queryModeSelect) {
      queryModeSelect.addEventListener('change', () => {
        currentQueryMode = queryModeSelect.value;
        currentPage = 1;
        render();
      });
    }

    syncSearchControls();
    render();
  </script>
</body>
</html>
"""



def export_html(records: List[Dict], query_tags: List[str], mode: str, output_file: str, export_limit: int = DEFAULT_EXPORT_LIMIT) -> str:
    output_path = Path(output_file).resolve()
    query_text = " ".join(query_tags) if query_tags else "全部记录"
    rows = list(records)
    if export_limit > 0:
        rows = rows[:export_limit]

    payload = []
    for rec in rows:
        payload.append({
            "title": rec.get("preview_title") or "(无标题)",
            "preview_url": rec.get("preview_url") or "",
            "original_url": rec.get("original_url") or "",
            "hashtags": ["#" + t for t in rec.get("hashtags", [])],
            "rating": safe_float(rec.get("rating"), 0),
            "fav_count": safe_int(rec.get("fav_count"), 0),
            "publish_date_raw": rec.get("publish_date_raw") or "",
            "publish_date_iso": rec.get("publish_date_iso") or "",
            "seq": len(payload),
        })

    page = HTML_TEMPLATE
    page = page.replace("__QUERY_TEXT__", html.escape(query_text))
    page = page.replace("__QUERY_MODE__", html.escape(mode))
    page = page.replace("__EXPORT_LIMIT__", str(export_limit))
    page = page.replace("__PAGE_SIZE__", str(PAGE_SIZE))
    page = page.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
    page = page.replace("__QUERY_TAGS__", json.dumps([normalize_tag(t) for t in query_tags if normalize_tag(t)], ensure_ascii=False))
    output_path.write_text(page, encoding="utf-8")
    return str(output_path)



def interactive_mode(records: List[Dict], html_file: str, export_limit: int) -> None:
    print("已进入交互模式。直接回车退出。")
    while True:
        raw = input("\n请输入一个或多个 hashtag: ").strip()
        if not raw:
            break
        tags = raw.split()
        mode = input("匹配模式 any/all [默认 any]: ").strip().lower() or "any"
        if mode not in {"any", "all"}:
            mode = "any"
        sort_by = input("排序方式 fav/rating [默认 fav]: ").strip().lower() or "fav"
        if sort_by not in {"fav", "rating"}:
            sort_by = "fav"
        period = input("时间范围 all/month/week [默认 all]: ").strip().lower() or "all"
        if period not in {"all", "month", "week"}:
            period = "all"
        window_index = 0
        if period in {"month", "week"}:
            raw_window = input("窗口序号 [默认 1，1=最新窗口]: ").strip() or "1"
            window_index = max(0, safe_int(raw_window, 1) - 1)
        do_export = input("是否导出 HTML? y/n [默认 y]: ").strip().lower() or "y"

        display_records = get_display_records(records, tags, mode, period, window_index)
        result = sort_records(display_records, sort_by)
        print_records(result[:50])
        print(f"共找到 {len(result)} 条结果。")
        if do_export == "y":
            out = export_html(records, tags, mode, html_file, export_limit=export_limit)
            print(f"已导出 HTML: {out}")



def main() -> None:
    parser = argparse.ArgumentParser(description="通过 JSONL / JSON / txt 数据搜索并导出可点击预览链接的 HTML 页面")
    parser.add_argument("tags", nargs="*", help="一个或多个 hashtag，例如 #汉语 #翻译 sakuram")
    parser.add_argument("--file", default=DATA_FILE, help=f"数据文件路径，默认: {DATA_FILE}")
    parser.add_argument("--mode", choices=["any", "all"], default="any", help="多个标签的匹配方式")
    parser.add_argument("--sort", choices=["fav", "rating"], default="fav", help="排序方式")
    parser.add_argument("--period", choices=["all", "month", "week"], default="all", help="时间范围")
    parser.add_argument("--window-index", type=int, default=0, help="最近窗口序号，0=最新窗口，1=第二个窗口")
    parser.add_argument("--top", type=int, default=50, help="命令行最多打印多少条结果")
    parser.add_argument("--export-html", action="store_true", help="导出 HTML 页面")
    parser.add_argument("--html-file", default=DEFAULT_HTML_FILE, help=f"导出的 HTML 文件名，默认: {DEFAULT_HTML_FILE}")
    parser.add_argument("--export-limit", type=int, default=DEFAULT_EXPORT_LIMIT, help=f"导出网页最多包含多少条记录，默认: {DEFAULT_EXPORT_LIMIT}")
    parser.add_argument("--interactive", action="store_true", help="进入交互模式")
    args = parser.parse_args()

    records = load_records(args.file)
    if not records:
        print("未能从数据文件中解析出有效记录。请检查文件内容格式。")
        return

    if args.interactive or not args.tags:
        interactive_mode(records, args.html_file, args.export_limit)
        return

    display_records = get_display_records(records, args.tags, args.mode, args.period, args.window_index)
    result = sort_records(display_records, args.sort)
    if args.top > 0:
        print_records(result[:args.top])
        print(f"共找到 {len(result)} 条结果，已打印前 {min(args.top, len(result))} 条。")
    else:
        print_records(result)
        print(f"共找到 {len(result)} 条结果。")

    if args.export_html:
        out = export_html(records, args.tags, args.mode, args.html_file, export_limit=args.export_limit)
        print(f"已导出 HTML: {out}")


if __name__ == "__main__":
    main()
