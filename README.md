# Telegram ExHentai 5 Star Crawler

一个用于整理 Telegram 频道 `@exhentai5star` 消息的本地辅助项目。项目包含两个脚本：

- `scrape_exhentai5star_json.py`：使用 Playwright 打开 Telegram Web，滚动读取频道消息，并将解析出的标签、评分、收藏数、预览链接、原始链接和发布日期增量保存为 JSONL。
- `search_exhentai5star_from_txt_html.py`：读取 JSONL / JSON / 原始 HTML 文本，按 hashtag 过滤、排序，并可导出一个可点击链接的本地 HTML 检索页面。

> 本项目仅用于个人数据整理和学习研究。请遵守 Telegram、E-Hentai / ExHentai 以及相关站点的服务条款，只处理你有权访问和保存的内容。不要将个人登录态、Cookie、抓取结果或可能含有隐私的数据提交到公开仓库。

## 功能

- 复用本地 Telegram Web 登录态，无需每次重新登录。
- 增量写入 JSON Lines 数据文件，重复运行时按记录 ID 去重合并。
- 提取 hashtag、评分、收藏数、预览链接、原始地址、发布日期等字段。
- 支持命令行搜索、交互式搜索和 HTML 检索页导出。
- HTML 检索页支持标签二次筛选、任一/全部匹配、收藏数/评分排序和分页。

## 环境要求

- Python 3.9 或更高版本
- Chromium 浏览器运行时，由 Playwright 安装
- 可访问 Telegram Web 的网络环境
- 已登录或可手动登录 Telegram Web 的账号

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
```

如果不使用虚拟环境，也可以直接执行：

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 使用方法

### 1. 抓取 Telegram 频道记录

```powershell
python scrape_exhentai5star_json.py
```

首次运行会打开一个 Playwright 管理的 Chromium 窗口。如果页面提示登录 Telegram Web，请在弹出的浏览器里手动完成登录。登录状态会保存在 `pw_telegram_profile/`，后续运行会复用。

抓取结果默认写入：

```text
exhentai5star_records.jsonl
```

### 2. 按标签搜索

```powershell
python search_exhentai5star_from_txt_html.py "#tag1" "#tag2" --mode all --sort fav --period all
```

常用参数：

- `--mode any|all`：多个标签任一匹配或全部匹配，默认 `any`
- `--sort fav|rating`：按收藏数或评分排序，默认 `fav`
- `--period all|month|week`：搜索全部记录、最近 400 条窗口或最近 100 条窗口，默认 `all`
- `--window-index 0`：当使用 `month` 或 `week` 时选择窗口，`0` 表示最新窗口
- `--top 50`：命令行最多打印多少条结果
- `--file path/to/data.jsonl`：指定输入数据文件

### 3. 导出 HTML 检索页

```powershell
python search_exhentai5star_from_txt_html.py "#tag1" "#tag2" --mode all --sort fav --period all --export-html
```

默认会生成：

```text
search_results.html
```

打开该文件即可在浏览器中进行本地检索和跳转。

### 4. 进入交互模式

```powershell
python search_exhentai5star_from_txt_html.py --interactive
```

交互模式会逐步询问标签、匹配方式、排序方式、时间窗口和是否导出 HTML。

## 数据格式

`exhentai5star_records.jsonl` 每行是一条 JSON 记录，字段示例：

```json
{
  "id": "sha1-record-id",
  "hashtags": ["tag1", "tag2"],
  "rating": 4.8,
  "fav_count": 1234,
  "preview_url": "https://telegra.ph/example",
  "preview_title": "Preview title",
  "original_url": "https://example.invalid/original-gallery-url",
  "publish_date_raw": "May 3",
  "publish_date_iso": "2026-05-03"
}
```

## 不应提交到 GitHub 的文件

以下文件或目录会由脚本运行生成，可能包含账号登录态、浏览记录、个人偏好或抓取数据，已经在 `.gitignore` 中排除：

- `pw_telegram_profile/`
- `exhentai5star_records.jsonl`
- `search_results.html`
- `*.jsonl`
- `*.html`
- `.venv/`
- `__pycache__/`

如果需要分享示例数据，建议手动脱敏后另存为 `examples/` 下的小样本文件。

## 项目结构

```text
.
├── scrape_exhentai5star_json.py          # 抓取 Telegram Web 频道消息到 JSONL
├── search_exhentai5star_from_txt_html.py # 搜索记录并导出 HTML
├── requirements.txt                      # Python 依赖
├── .gitignore                            # Git 忽略规则
└── README.md                             # 使用说明
```

## 常见问题

### Telegram 页面一直等待或无法加载

确认网络环境可以访问 `https://web.telegram.org/`，并在弹出的浏览器窗口中完成登录。如果登录态异常，可以关闭脚本后删除本地 `pw_telegram_profile/` 再重新登录。

### 抓取到的记录数量没有增加

脚本会向上滚动频道消息，并在连续多轮没有新增记录后停止。如果频道 DOM 结构变化，可能需要更新选择器：

- `div.message`
- `div.message.spoilers-container`
- `a.anchor-hashtag`
- `a.anchor-url`

### HTML 导出文件很大

默认 `--export-limit` 为 `500000`。如果数据量很大，可以降低导出上限：

```powershell
python search_exhentai5star_from_txt_html.py "#tag1" --export-html --export-limit 5000
```

## 许可证

当前仓库尚未声明开源许可证。发布到公开 GitHub 仓库前，请根据你的分享意图选择合适的许可证，或保持默认的“保留所有权利”状态。
