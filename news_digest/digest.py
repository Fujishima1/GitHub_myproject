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
        "あなたはAI・メディア業界の実務家向けニュースダイジェストを作成する編集者です。\n"
        "以下は過去24時間に収集した記事一覧です。この中から、その日読むべき重要なニュースを選び、"
        "日本語で1〜2文の要約を書いてください。\n\n"
        "## 選定の優先順位\n"
        "1. 全体として「メディア関連ニュース」を「AI関連ニュース」より優先してください(メディア > AI)。\n"
        "2. メディア関連の中でも特に重視するテーマ: 新聞、Webメディア、ニュースアプリ、"
        "サブスクリプション、メディア×AI(生成AIとメディア業界の交差領域)、"
        "メディア企業の経営(買収・資金調達・業績・組織再編など)。\n"
        "3. 上記以外のメディア関連ニュース(例: 一般的な広告・マーケティング動向など)は、"
        "完全に除外はせず、重要度をわずかに下げる程度で扱ってください。\n"
        "4. 地域は日本のニュースを中心にしつつ、米国・中国の主要ニュースも積極的に取り上げてください。"
        "欧州については、日本への影響が大きい、または参考になる大きなニュースに絞ってください。\n\n"
        "## その他のルール\n"
        "- 同じ出来事について「報道記事」と「公式発表(プレスリリース等)」の両方が一覧にある場合は、"
        "1つのトピックとしてまとめ、両方の番号を indices に含めてください。\n"
        "- 件数の目安は1日5〜10本ですが、重要なニュースが多い日は10本を超えても構いません。"
        "逆に重要な記事が少なければ無理に5本に増やさなくて構いません。\n"
        "- 重複した内容や広告的な記事は除外してください。\n\n"
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
                                    "indices": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                        "minItems": 1,
                                        "maxItems": 3,
                                    },
                                    "summary_ja": {"type": "string"},
                                },
                                "required": ["indices", "summary_ja"],
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
        indices = [i for i in item.get("indices", []) if 0 <= i < len(entries)]
        if not indices:
            continue
        grouped = [entries[i] for i in indices]
        results.append(
            {
                "title": grouped[0]["title"],
                "category": grouped[0]["category"],
                "summary_ja": item.get("summary_ja", ""),
                "links": [{"source": g["source"], "url": g["link"]} for g in grouped],
            }
        )
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
            rows = []
            for a in items:
                link_list = " / ".join(
                    f"<a href='{link['url']}'>{link['source']}</a>" for link in a["links"]
                )
                rows.append(
                    f"<li style='margin-bottom:12px;'>"
                    f"<div style='font-weight:bold;'>{a['title']}</div>"
                    f"<div style='color:#555;font-size:14px;'>{a['summary_ja']}</div>"
                    f"<div style='color:#999;font-size:12px;'>{link_list}</div>"
                    f"</li>"
                )
            sections.append(f"<h3>{category}</h3><ul>{''.join(rows)}</ul>")
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
