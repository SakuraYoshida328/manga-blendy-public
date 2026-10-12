# MB外部連携の実用契約

アプリ固有のSDKを使わず、保存背景を検索し、1枚のPNGと設定JSONを取り出します。他アプリや原稿へ書き込みません。版1の出力は統合PNGであり、PSD/CSPや主線・トーン分離は未対応です。

## CLI

アプリのフォルダで実行します。`--projects <資料集ルート>` はサブコマンドより前に指定できます。

```powershell
.venv/Scripts/python.exe -m manga_blendy list --query '教室'
.venv/Scripts/python.exe -m manga_blendy master project_EXAMPLE
.venv/Scripts/python.exe -m manga_blendy request project_EXAMPLE request.json --run
.venv/Scripts/python.exe -m manga_blendy finish-export project_EXAMPLE job_EXAMPLE
.venv/Scripts/python.exe -m manga_blendy export-image project_EXAMPLE job_EXAMPLE --camera source
```

IDは検索結果の実値へ置き換えます。`request` は既定ではキューに入れるだけ、`--run` で単発実行します。通常のWebワーカーと別CLIワーカーを同時に実行しないでください。

現在の `request.json`：

```json
{
  "stage": "production_drawing",
  "camera_id": "source",
  "workflow": "two_stage",
  "render": {
    "focus_mode": "character",
    "character_outline_px": 1.6,
    "detail_level": "medium",
    "resolution": [1448, 1086]
  }
}
```

工程は本番 `production_drawing` か資料検証 `reference_validation`。ネーム・絵コンテは拒否します。モードは `character`、`balanced`、`background`、方式は `one_stage`／`two_stage`。カメラは保存済みIDを指定。線幅と細部は別の値で、構造JSONを書き換えません。任意Pythonや外部への書込先は指定できません。

`master --state <state_id>`、`request --state <state_id>` で保存した場面状態を明示できます。未指定時は現在の採用状態。二段階の完成出力はWebの候補取込と承認が終わるまで拒否されます。

## ローカルHTTP

Web起動時だけ `127.0.0.1:8765` を使います。

| 方法・パス | 結果 |
| --- | --- |
| GET `/api/backgrounds?q=教室` | 名前／ID検索 |
| GET `/api/backgrounds/{id}/master?state_id={state}` | マスター、状態、座標系、カメラ |
| POST `/api/backgrounds/{id}/render` | 上記の要求と任意の `state_id` を同じJSONに入れ、ジョブID取得 |
| GET `/api/projects/{id}/jobs/{job}` | `workflow_status`、`output_ready` と作業状態 |
| POST `/api/backgrounds/{id}/jobs/{job}/export-image` | `{"camera_id":"source"}` でPNG＋JSON出力 |
| GET `/projects/{id}/jobs/{job}/finish/handoff` | 二段階の受け渡しZIP |
| POST `/api/projects/{id}/exterior` | multipartの画像で屋外2D交換の未採用候補 |
| POST `/api/projects/{id}/improvements` | `{"description":"改善案","scope":"rendering"}` を保存 |
| POST `/api/projects/{id}/improvements/{proposal}/approve` | `{"approved":true,"expected_revision":"revision_EXAMPLE"}` で明示承認 |

更新POSTには、ホームなどで得たセッションCookieとHTMLの `meta[name=manga-token]` を `x-manga-token` として付けます。画像のmultipartフォームでは `token` フィールドでも渡せます。値をログ、配布物、連携JSONへ保存しません。外部APIキーを新設せず、ローカルの利用者セッションを使います。ネットワーク公開用の認証ではありません。

## 出力と保全

`exports/images/{hash}/background.png` と `background.json` に、背景ID、改訂・状態ID、数値カメラ、座標系、最終寸法、画像ハッシュ、線・詳細設定、構造／承認済み漫画という種類を保存します。画像パスは相対、画像は勝手に拡大縮小しません。資料集全体のZIPとは別の小さな受け渡しです。

遠景交換は新しい改訂でプレビューし、採用前は元マスターを保持。新画像は独立asset IDとハッシュを持ち、Blenderにパックされます。室内・窓・ベランダ・カメラを変えないことを検査します。現在は遠景の方向対応で、近景視差や奥行き別レイヤーは未対応です。旧シーンに遠景ポリゴンが残る場合は検査で拒否し、任意メッシュを自動削除しません。
