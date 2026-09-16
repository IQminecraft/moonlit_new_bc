# moonlit_build_card — 構造リファレンス

原神ビルドカード生成サイト（FastAPI + Discord bot）。uid のショーケースを Enka API から取り、
HTML カード（正典）と PIL 画像の両方で編成/単体カードを共有できる。

## 全体レイヤ

```
                    ┌─────────────────────────────────────────────┐
 クライアント        │ templates/build_card.html（カードUI・正典）    │
                    │ artifacter.html（UID検索） admin.html / contact │
                    └───────────────┬─────────────────────────────┘
                                    │ HTTP（fetch / ページ遷移）
                    ┌───────────────▼─────────────────────────────┐
 エントリ           │ server.py ── FastAPI + uvicorn（PORT 既定8000）│
                    │  StaticFiles(static/)  mount・router登録      │
                    └───────┬───────────────┬─────────────────────┘
                            │               │
              ┌─────────────▼──────┐  ┌────▼─────────────────────┐
 ルーター      │ routes/api.py       │  │ routes/admin.py           │
              │ 公開: /uid /fetch_uid│  │ 管理: 更新情報・アセット・   │
              │ card/team 画像生成   │  │ leyline・幽境レイアウト     │
              │ 共有API・/s /share   │  └───────────────────────────┘
              └─────────────┬──────┘
                            │
        ┌───────────────────▼───────────────────────────┐
 生成コア │ card/: data(単体card_data計算) → calc_method / │
        │  stat_calc / stats(表記・丸め) / resonance /     │
        │  set_buffs → image(単体PIL) team_image(編成PIL)   │
        │  theme_cards テーマ描画 / draw / labels / region  │
        │  bg / cache / pool(生成プール) / sign(URL署名)     │
        │  / jsoncache(ディスクJSON LRU)                   │
        └───────────────────┬───────────────────────────┘
                            │
              ┌─────────────▼──────────────┐
 データ取得    │ get_info_state.py           │
              │  Enka SDK / CNはMicroGG     │
              │  → static/cache/showcase_   │
              │    {uid}.json（唯一の生データ）│
              └────────────────────────────┘
```

## ディレクトリ

| パス | 役割 |
|---|---|
| `server.py` | 起動のみ（app生成・mount・イベント） |
| `app/paths.py` | BASE_DIR / STATIC_DIR / SHARE_DATA_DIR / テンプレート / フォント / カード寸法定数 |
| `app/routes/api.py` | 公開API全経路（ページ・card_data・画像生成・共有・shortリンク） |
| `app/routes/admin.py` | 管理API（認証は admin_data の token） |
| `app/routes/params.py` | クエリパラメータのクリーンアップ（clean_uid 他） |
| `app/card/` | カード計算とPIL描画（上記レイヤ図の生成コア） |
| `app/convert/` | 外部ゲームデータ → 站内JSON（lunaris/nanoka/enka）変換スクリプト群 |
| `app/core/` | notify(Discord error webhook) / jsonio / server_stats / api_log(ミドルウェア) |
| `app/get_info_state.py` | Enka/MicroGG 取得とショーケース保存・整形 |
| `app/admin_data.py` / `app/gachabase_changelog.py` | 管理データストア / beta変更ログ |
| `bot/bot.py` | Discord bot（UID→カード画像送信、refresh_uid 連携） |
| `templates/` | build_card.html（単体+編成+幽境+共有閲覧、10k行級SPA風）/ artifacter / admin |
| `static/` | fonts / assets（キャラ・武器・背景）/ data（ゲームデータJSON）/ cache（生成キャッシュ）/ beta（先行データ） |
| `share_data/` | 編成共有ランタイムデータ（snapshots=編成凍結 / short=リンク圧縮 / abyss_drafts=幽境下書き / share_phrases=合言葉） |
| `tools/` | QA用使い捨てスクリプト・テストリンク |

## 主なデータフロー

1. **表示**: ブラウザ → `/uid/{uid}`（クールタイム明けは自動再取得）→ `showcase_{uid}.json`（無ければEnka）→ `/api/card_data/...` で card_data JSON → HTMLカード描画（正典）。
2. **画像生成**: `card_sign / team_card_sign` で署名付きURL → PIL（card.image / card.team_image）。`cache=server` のとき `static/cache/cards/`。
3. **編成共有**: 作成時 `/api/share_snapshot`（card_data + card_std を凍結・sid）→ 圧縮リンク `/share/{hex}`（短縮 `/s/{id}`）→ 閲覧者は sid だけで表示、enka 参照なし。**HTMLも画像タブも同じ凍結JSON**を使う（画像は cards モード POST、UID非公開でも可・30日TTL）。
4. **幽境共有**: admin のレイアウトJSON + baseimg で PIL / DOM 双方描画。下書きは `share_data/abyss_drafts/`。

## 制約・流儀（実装から読める不文律）

- HTMLカード（build_card.html）が正典。PIL側（card/team_image.py）は同じcard_data形式を1:1で描く同期関係。
- card_data の標準丸めは `card_std`（スナップショット側で凍結済）またはクライアント側 `standardizeCardData`。精度切替はサーバー再取得なしのローカル処理。
- 生成系は専用スレッドプール（card.pool、workers=2/queue=20/timeout=8s）+ URL署名（card.sign、CARD_URL_SECRET）+ IPレート制限。
- 画像はPNG固定（WEBP廃止）、`_CARD_CACHE_VERSION` 更新でディスクキャッシュ無効化。
- 設定JSONは `static/data/setting/`（ui_flags・abyss_share_layout 等）、ランタイム共有データは `share_data/` に分離（git対象外）。
- Tailwind は **CDN ではなく自前ビルド**（`static/tailwind.build.css` を templates で link）。CDNはFirefoxのトラッキング防止でブロックされレイアウトが崩れるため使用禁止。
  クラスを追加・変更したら再ビルドが必要:
  `npx -y tailwindcss@3.4.16 -i <(echo "@tailwind base;@tailwind components;@tailwind utilities;") -o static/tailwind.build.css --content "templates/**/*.html"`

構造図PNG: `docs/structure.png`（このmdと同じ内容を図にしたもの）
## 共有データの保存バックエンド（share_store）

編成共有のランタイムデータ（snapshots / short / abyss_drafts）は `app/share_store.py` 経由で
**ローカル share_data/ と Cloudflare R2 を環境変数で切替**（既定 local、R2設定不備はwarningしてlocalへフォールバック）。

- 環境変数: `SHARE_BACKEND=local|r2`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`
- key 例: `snapshots/s….json` / `short/<id>.json` / `abyss_drafts/<uid>_live.json`
- 期限: 幽境編成共有=`exp:0`（無期限）、通常編成共有=30日（shortは閲覧のたびローリング）
- 合言葉引き継ぎ（share_phrases）は短期データのため常にローカル
- ローカル→R2 の初回移行: `python tools/share_migrate_to_r2.py`（`--apply` で実行、`--delete` で移行後ローカル削除）
