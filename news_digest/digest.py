"""毎朝、AI・メディア関連ニュースを収集してメールで送るスクリプト。

処理の流れ:
  1. feeds.yaml に列挙されたRSSフィードから、直近24時間分の記事を集める
  2. 集めた記事をまとめてClaude APIに送り、重要な記事を選んで日本語で要約させる
  3. 選ばれた記事をHTMLメールに整形し、Gmail(SMTP)で送信する

必要な環境変数:
  ANTHROPIC_API_KEY   Claude APIキー
  GMAIL_ADDRESS       送信元Gmailアドレス
  GMAIL_APP_PASSWORD  Gmailのアプリパスワード(通常のログインパスワードではない)
  DIGEST_RECIPIENT    配信先メールアドレス
"""

import datetime
import json
import os
import re
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path

import anthropic
import feedparser
import yaml

FEEDS_PATH = Path(__file__).parent / "feeds.yaml"
LOOKBACK_HOURS = 26  # 毎日cronの間隔(24h)より少し長めに取り、取りこぼしを防ぐ
MAX_SELECTED_ARTICLES = 15
MODEL = "claude-sonnet-5"


def load_feeds() -> list[dict]:
    with FEEDS_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)["feeds"]


def fetch_recent_entries(feeds: list[dict]) -> list[dict]:
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        hours=LOOKBACK_HOURS
    )
    entries = []
    for feed in feeds:
        try:
            parsed = feedparser.parse(feed["url"])
        except Exception as exc:  # noqa: BLE001 - フィード取得失敗は継続したい
            print(f"[WARN] フィード取得エラー: {feed['name']} ({feed['url']}): {exc}", file=sys.stderr)
            continue

        if not parsed.entries:
            print(f"[WARN] フィードから記事を取得できませんでした: {feed['name']} ({feed['url']})", file=sys.stderr)
            continue

        for e in parsed.entries:
            published_struct = e.get("published_parsed") or e.get("updated_parsed")
            if published_struct:
                published_dt = datetime.datetime(
                    *published_struct[:6], tzinfo=datetime.timezone.utc
                )
                if published_dt < cutoff:
                    continue

            entries.append(
                {
                    "source": feed["name"],
                    "category": feed.get("category", ""),
                    "title": (e.get("title") or "(無題)").strip(),
                    "link": e.get("link", ""),
                    "snippet": re.sub(r"<[^>]+>", "", e.get("summary", ""))[:400],
                }
            )
    return entries


def select_and_summarize(entries: list[dict], api_key: str) -> list[dict]:
    if not entries:
        return []

    client = anthropic.Anthropic(api_key=api_key)

    listing = "\n\n".join(
        f"[{i}] カテゴリ:{e['category']} / 情報源:{e['source']}\n"
        f"タイトル:{e['title']}\n"
        f"概要:{e['snippet']}"
        for i, e in enumerate(entries)
    )

    prompt = (
        "以下は過去24時間にAI・メディア業界関連のRSSフィードから収集した記事一覧です。\n"
        f"この中からAI業界・メディア業界にとって特に重要・注目すべき記事を最大{MAX_SELECTED_ARTICLES}件選び、"
        "それぞれ日本語で1〜2文の簡潔な要約を書いてください。\n"
        "重複した内容や広告的な記事は除外してください。重要な記事が少なければ件数を無理に増やさなくて構いません。\n\n"
        f"{listing}"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {
                        "selected": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "index": {"type": "integer"},
                                    "summary_ja": {"type": "string"},
                                },
                                "required": ["index", "summary_ja"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["selected"],
                    "additionalProperties": False,
                },
            }
        },
    )

    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)

    results = []
    for item in data.get("selected", []):
        idx = item.get("index")
        if idx is None or not (0 <= idx < len(entries)):
            continue
        results.append({**entries[idx], "summary_ja": item.get("summary_ja", "")})
    return results


def build_email_html(articles: list[dict], today: str) -> str:
    if not articles:
        body = "<p>本日は該当するニュースが見つかりませんでした。</p>"
    else:
        by_category: dict[str, list[dict]] = {}
        for a in articles:
            by_category.setdefault(a["category"], []).append(a)

        sections = []
        for category, items in by_category.items():
            rows = "\n".join(
                f"<li style='margin-bottom:12px;'>"
                f"<a href='{a['link']}' style='font-weight:bold;'>{a['title']}</a>"
                f"<div style='color:#555;font-size:14px;'>{a['summary_ja']}</div>"
                f"<div style='color:#999;font-size:12px;'>{a['source']}</div>"
                f"</li>"
                for a in items
            )
            sections.append(f"<h3>{category}</h3><ul>{rows}</ul>")
        body = "\n".join(sections)

    return f"""
    <html>
      <body style="font-family: -apple-system, sans-serif; max-width: 640px; margin: 0 auto;">
        <h2>AI・メディア ニュースダイジェスト ({today})</h2>
        {body}
      </body>
    </html>
    """


def send_email(html_body: str, today: str) -> None:
    sender = os.environ["GMAIL_ADDRESS"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ["DIGEST_RECIPIENT"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"AI・メディア ニュースダイジェスト ({today})"
    msg["From"] = sender
    msg["To"] = recipient
    msg["Date"] = formatdate(localtime=True)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender, app_password)
        server.sendmail(sender, [recipient], msg.as_string())


def main() -> None:
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime(
        "%Y-%m-%d"
    )

    feeds = load_feeds()
    entries = fetch_recent_entries(feeds)
    print(f"[INFO] 収集した記事数: {len(entries)}")

    articles = select_and_summarize(entries, os.environ["ANTHROPIC_API_KEY"])
    print(f"[INFO] 選定した記事数: {len(articles)}")

    html_body = build_email_html(articles, today)
    send_email(html_body, today)
    print("[INFO] メール送信完了")


if __name__ == "__main__":
    main()
