# my_project

## AI・メディア ニュースダイジェスト

毎朝8時(JST)に、AI関連・メディア業界関連のニュースを収集し、重要なものをClaude APIで要約してメールで届ける仕組みです。

**編集方針**(`digest.py` のプロンプトに反映済み):
- 重要度は「メディア > AI」。メディアの中でも新聞・Webメディア・ニュースアプリ・サブスク・
  メディア×AI・メディア企業の経営を最重視し、それ以外のメディア関連ニュースは少しだけ重要度を下げる
- 地域は日本中心+米国・中国の主要ニュース+欧州は日本への影響が大きいものだけ
- 同じ出来事の「報道記事」と「公式発表」がある場合はまとめて両方のリンクを掲載
- 本数の目安は1日5〜10本(重要なニュースが多ければ超えてもよい)

- 実行本体: `news_digest/digest.py`
- ニュース収集元の設定: `news_digest/feeds.yaml`
- 定期実行: `.github/workflows/daily-news-digest.yml` (GitHub Actions, 毎日 23:00 UTC = 08:00 JST)

### セットアップ手順

1. Gmailの「アプリパスワード」を発行する
   - Googleアカウントで2段階認証を有効化した上で、
     https://myaccount.google.com/apppasswords からアプリパスワードを発行してください。
   - 通常のGmailログインパスワードは使えません。
2. Anthropic APIキーを発行する(https://console.anthropic.com/ )
3. このリポジトリの Settings > Secrets and variables > Actions で、以下の4つを登録する

   | Secret名 | 内容 |
   |---|---|
   | `ANTHROPIC_API_KEY` | Claude APIキー |
   | `GMAIL_ADDRESS` | 送信元Gmailアドレス |
   | `GMAIL_APP_PASSWORD` | 手順1で発行したアプリパスワード |
   | `DIGEST_RECIPIENT` | 受信したいメールアドレス |

4. Secrets登録後、Actionsタブ > "Daily AI/Media News Digest" > "Run workflow" で手動実行し、
   メールが届くか確認する
   - `news_digest/feeds.yaml` 内のフィードURLはウェブ検索をもとに設定したものです。
     この開発セッション自体のネットワークポリシーでニュースサイトへの直接アクセスが
     ブロックされていたため(egressプロキシから403 Forbidden)、開発中にフィードの
     実取得確認はできませんでした。GitHub Actions実行環境は別ネットワークなので
     通常は問題なく取得できるはずですが、実行ログに `[WARN]` が出たフィードは
     URLを見直すか削除してください。
5. 動作確認できれば、あとは毎朝自動的に配信されます

### ニュース収集元を追加・変更したい場合

`news_digest/feeds.yaml` に `name` / `category` / `url` を1セット追加するだけです。コードの変更は不要です。