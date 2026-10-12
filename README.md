# Manga Blendy — v0.1.0-beta.1

Windows向けのローカル漫画背景設定資料アプリです。画像、空間構造、物体ID、参照画像、カメラ、場面ごとの状態を保存し、同じ背景を必要なコマだけ呼び出します。**簡略化が目的ではなく、繰り返し運用できることが目的**です。

一般公開ベータ版です。[配布ページ](https://github.com/SakuraYoshida328/manga-blendy-public/releases/tag/v0.1.0-beta.1)から誰でも取得でき、GitHubの招待・ログインは不要です。ソース配布の試用版で、未実装のAI工程と実機未検証の項目を下記に明記しています。

## Codexで導入する

[この公開リポジトリ](https://github.com/SakuraYoshida328/manga-blendy-public)のURLをCodexへ渡し、次のように依頼してください。

> このリポジトリの docs/INSTALL_WITH_CODEX.md を読んで、Manga Blendyをインストールしてください。PCを確認し、必要なBlender・MCPの導入と初回サンプル試験まで進めてください。インストール・重要設定の前に変更範囲をまとめて確認し、既存設定を保護してください。有料APIは使わないでください。

- [Codex用の導入手順](docs/INSTALL_WITH_CODEX.md)
- [導入・基本操作](docs/getting-started.md)
- [よくあるエラー](docs/TROUBLESHOOTING.md)
- [今回の検証範囲と公開監査](docs/PUBLIC_BETA_AUDIT.md)

[v0.1.0-beta.1のReleases](https://github.com/SakuraYoshida328/manga-blendy-public/releases/tag/v0.1.0-beta.1)から `manga-blendy-v0.1.0-beta.1-windows-source.zip` と `SHA256SUMS.txt` を取得してください。ソースを含むパッケージで、完成した単体EXEではありません。Python・Blender・MCPを別途導入します。ZIP利用ならGitは不要です。

## 実装した機能

- 画像と観測JSONの取込。確認した事実と推定を分けた設定資料集、改訂の候補・承認、連続場面の配置差分を保存。
- 建物／通常家具／布家具／通常小物／布小物を部品ごとに管理。軽量3Dと詳細画像をIDで対応させる。
- 保存3DをBlender/MCPで再利用し、数値カメラからPNGを出力。構造をカメラごとに再解釈しない。
- 人物主役／バランス／背景主役の3モード。人物線の基準幅、細部強度、トーンを個別に指定。
- 二段階仕上げの構造PNG・参照・編集指示ZIPを出力し、外部編集PNGを候補として取込。構造・外観の確認後だけ完成出力。
- 遠景の2D参照を候補改訂で交換。資料集ZIPの保存・新規IDでの再読込み、CLIとローカルAPIによる他アプリ連携。
- BlenderとAIは必要時のみ利用。1コマは原則1カメラで、同じ要求は保存済み結果を再利用。

## 実際の作例

以下は本アプリの合成サンプルから実際に生成した画像です。個人の原稿・写真、外部アセットを使用していません。**手定義した一室の構造試験**であり、写真からの自動復元や完成漫画の品質を示す作例ではありません。生成方法・画像ハッシュは [作例の記録](docs/examples/README.md) にあります。

| 基準カメラ | 反対側カメラ |
| --- | --- |
| ![基準の構造プレビュー](docs/examples/source.png) | ![反対側の構造プレビュー](docs/examples/right.png) |

![保存3DをMCPで単発撮影した出力](docs/examples/mcp-balanced.png)

最後の画像は保存済み3Dに線・トーン設定を適用した直接出力です。布の自然な描写など、複雑な漫画仕上げには外部編集と人の確認が必要です。私的な成功作例は配布に含めません。

## 対応環境・PCスペック

| 項目 | ベータの対象・検証範囲 |
| --- | --- |
| OS | Windows 11 x64。別PC、Windows 10、ARM64は実機未検証 |
| Python | 3.12以上。3.12.14 x64で確認。新規候補3.14 x64は依存解決のみ確認、実行未検証 |
| PowerShell | 7推奨。配布版はPS1をUTF-8 BOM付きにし、標準5.1でも構文・起動を確認済み |
| Blender | 5.2、検証版5.2.0 LTS。別版の互換を保証しない |
| MCP | 指定のBlender側アドオン1.8／protocol 13。固定コミット・SHAは導入手順参照 |
| 検証PC | Core i7-10700、RAM 32GB、GeForce RTX 2070 SUPER、Windows 11 |
| 導入の目安 | 8コア級CPU、RAM 32GB、VRAM 8GB級GPU、書込み可能な短いローカル保存先。性能保証値ではない |
| 空き容量 | ソフトと依存用に10GB以上を目安に確保し、資料・PNG・.blend用を別に追加。上限はデータ量次第 |

Blender公式のWindows最低要件は4コア／SSE4.2、RAM 8GB、VRAM 2GBと所定のグラフィックスAPI対応です。ただしこの最低構成でMBを試験していません。[公式要件](https://www.blender.org/download/requirements/) でGPU条件を確認してください。CPUでの試験を行っており、GPUレンダリング速度を約束しません。

Python 3.12.14の公式Windowsインストーラーはないため、既存の動く版を優先するか [公式Windows配布](https://www.python.org/downloads/windows/) を確認します。導入説明はCodex内部のPythonキャッシュを必須にしません。

## AI利用・料金・機能制限

**本体からの自動AI画像解析と自動漫画画像生成は未実装です。** 現行は外部のCodex／ChatGPTで解析JSONや編集PNGを作り、本体へ取り込みます。任意の「Continue with ChatGPT」は、利用者自身のアカウントで行うモデル一覧の接続試験です。モデルIDを固定せず、ログイン成功を本体AI機能の完成とは扱いません。

APIキー経路は無効です。APIの追加課金や有料クレジットに自動切替しません。Plus等の対象条件は [OpenAI公式資料](https://developers.openai.com/siwc/token-sharing-open-source) に従い、本人が追加クレジットを使わない設定を確認します。利用枠が切れたら外部AI工程を停止します。本体は外部アプリの残量を自動監視しません。プレビューの画像生成ツールは [未対応](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations) です。

完全一致、見えない面の正解、特殊家具・自然な布の完全自動復元は保証しません。PSD/CSP、線・トーンの独立レイヤー、他アプリへの自動貼付け、自動アップデートは未実装です。遠景2Dは近景の視差や未知の全周景観には対応しません。

別Windows PCへの新規導入、Python 3.14実行、MCP新規GUI導入、別利用者の初回ChatGPT認証は**実機未検証**です。ベータでは成功・失敗・未実施を分けて報告してください。

## 保存と不具合報告

資料は `projects/`、端末設定は `config.json`、試験記録は `.work/` に保存します。更新前に資料集ZIPを保存し、新版は別フォルダへ導入します。[保存・復元](docs/getting-started.md) に従ってください。

[GitHub Issues](https://github.com/SakuraYoshida328/manga-blendy-public/issues/new/choose)には不具合／機能要望テンプレートを使ってください。原稿・個人画像・ユーザー名入りパス・トークンを送らず、同梱サンプルで再現してください。セキュリティ問題は [SECURITY.md](SECURITY.md) を参照してください。

## ライセンス・設計資料

ソース・文書・合成作例は **GPL-3.0-or-later**です。所有者の公開承認を受けて配布しています。[LICENSE](LICENSE)、[第三者表記](THIRD_PARTY_NOTICES.md)、[依存一覧](DEPENDENCIES.json) を添付しています。Blender/MCPやPython依存の実行ファイルは同梱しません。利用者自身の画像・漫画・.blendの権利をアプリのライセンスへ自動変更しません。

- [AI工程の実装境界](docs/AI-capabilities.md)
- [CLI/APIによる外部連携](docs/integration-interface.md)
- [素材5分類と漫画仕上げ](docs/素材分類と漫画仕上げ.md)
- [複雑な部屋の整理と画像連動](docs/複雑な部屋の整理と画像連動.md)
- [背景エンジン連動仕様](docs/背景エンジン連動仕様.md)
- [ベータ変更点](RELEASE_NOTES.md)
