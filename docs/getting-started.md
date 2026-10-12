# Manga Blendy ベータ版：導入と基本操作

Windowsで初めて使う人向けの手順です。Codexに任せる場合は [INSTALL_WITH_CODEX.md](INSTALL_WITH_CODEX.md) を指定してください。困った場合は [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

## 1. できることと導入のゴール

画像と解析JSONから背景の設定資料を保存し、軽量3Dと参照画像を使って同じ空間を再利用します。人物主役・バランス・背景主役を選び、必要な背景だけ出力します。

最初は手定義の合成一室で、アプリ起動、Blender/MCP、PNGと.blendの保存、資料ZIPの再読込みを確認します。元原稿や私的画像は不要です。このテストにChatGPTログインは不要で、漫画の完成品質を判定するテストでもありません。

現行のAI解析は外部のCodex/ChatGPTで作ったJSONの取込、漫画仕上げは外部で編集したPNGの取込です。ログイン成功だけで自動AI解析・自動画像生成が動くわけではありません。

## 2. 利用するPCと許可

基本構成はWindows 11 x64、PowerShell 7、Python 3.12以上、Blender 5.2、ブラウザ、インターネット。Gitで取得するならGitが必要です。公開版のcloneは認証不要です。確認済み端末はPython 3.12.14、PowerShell 7.6.5、Blender 5.2.0 LTS。**別PCへの新規導入は実機未検証**です。

新規Python候補3.14 x64は固定依存の解決を確認しましたが、アプリ実行は実機未検証です。Windows 10、ARM64、他版のBlenderも実機未検証。動く既存環境を優先し、Codexの内部キャッシュを全PCの前提にしません。

Codexは最初にPCを読み取り確認し、不足ソフト・依存・保存先・アドオン・設定保存・サンプル生成をまとめて提案します。許可するまではインストールしません。本人のログインだけはご自身で行います。パスワードやトークンをチャットへ送る必要はありません。

PowerShell 7は [Microsoft公式](https://learn.microsoft.com/en-us/powershell/scripting/install/install-powershell-on-windows)、Pythonは [公式Windows配布](https://www.python.org/downloads/windows/)、Gitは [公式Windows配布](https://git-scm.com/install/windows) から導入します。Pythonにはpipを含めます。3.12.14は公式Windowsインストーラーがないため、その架空のダウンロードURLを使いません。

## 3. アプリの取得

一般公開後は公開リポジトリのReleasesから `manga-blendy-v0.1.0-beta.1-windows-source.zip` と `SHA256SUMS.txt` を取得できます。招待・GitHubログインは不要です。この候補の準備段階ではまだ公開していません。

Codexに公開URLを渡すと、INSTALL_WITH_CODEX.mdの工程2に従い、ハッシュを確認して新しい空フォルダへ展開します。Gitでのcloneも可能ですが、ZIP利用ならGitを追加導入する必要はありません。私的な開発リポジトリを取得先にしません。

READMEとsetup.ps1のあるフォルダを開きます。アプリルートは60文字程度までを目安に短くし、クラウド同期フォルダ・既存フォルダの上書きを避けます。必要ソフトと保存先への変更は最初にまとめて許可してください。

## 4. Pythonと依存をセットアップする

PowerShell 7でアプリのフォルダを開きます。`python`や`py -0p`で実際のPythonを確認するか、Codexに調べてもらい、そのフルパスを入力します。

```powershell
$mbPython = Read-Host '確認したPython実行ファイルのフルパス'
& $mbPython -c "import sys;print(sys.version);assert sys.version_info >= (3,12)"
& $mbPython -m pip --version
pwsh -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -PythonPath $mbPython
if ($LASTEXITCODE) { throw 'セットアップ失敗。次の工程を止めてください' }
& .\.venv\Scripts\python.exe -c "import manga_blendy,fastapi,PIL,numpy,scipy;print('MB_IMPORT_OK')"
```

`MB_IMPORT_OK`が成功の確認です。setup.ps1は基底Pythonのpip（22.3以上）で依存を導入します。作成する.venvにはpipを入れないため、.venv側の「No module named pip」は想定内です。Bypassはこの起動プロセスだけで、PC全体の実行ポリシーは変えません。組織の制限がある場合は管理者に相談します。既存.venvは無断で作り直さないでください。

## 5. Blender 5.2とMCP

既存のBlender 5.2を使えるなら再インストールしません。なければ [公式5.2配布](https://download.blender.org/release/Blender5.2/) のWindows x64版を、許可した保存先へ導入します。5.2.0のMSIとZIPを確認済みです。他版を上書きしないZIP配置も使えます。実際のblender.exeを選び、`& $mbBlender --version`で5.2を確認します。

MCPはBlender側の第三者アドオンだけがMBに必要です。uvやCodex設定へのMCP追加は不要です。既存の動く接続を優先します。新規導入する版は [Codex手順の固定アドオン](INSTALL_WITH_CODEX.md#5-検証済みblender側mcpアドオン) に従い、コミットとSHA256を確認して取得します。

Blenderの編集 → プリファレンス → アドオン → Install from Diskでblender_mcp.pyを選び、有効化します。既存の同名アドオンや設定は許可なく上書きしません。Poly Haven、Sketchfab、Hyper3D、Hunyuan3D等の外部連携とAllow TelemetryはOFFにします。

3DビューでN → MCP for Blender → Port 9876 → Connect to MCP server。Connected on port 9876を確認します。既に接続済みなら二重に開始しません。作業後はDisconnectし、必要な時だけBlenderを使います。アドオンの版・操作は固定していますが、別PCの新規GUI導入は実機未検証です。

## 6. Manga Blendy起動と診断

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

`http://127.0.0.1:8765/`を開きます。起動したターミナルは閉じず、終了はCtrl+Cです。start.cmdは標準PowerShell 5.1を使います。公開候補のPS1はBOM付きですが、PowerShell 7を推奨します。

「導入・接続確認」（`/setup`）で、実際のBlenderパスを入力・保存し、接続確認します。BlenderとMCPの両方がready=trueなら診断成功です。診断はシーンを変更せず、AIも呼びません。CLIも使えます。

```powershell
& .\.venv\Scripts\python.exe -m manga_blendy diagnose
```

終了コードだけでなくJSONのblender.readyとmcp.readyを確認します。Webは8765、MCPは127.0.0.1:9876です。

## 7. 最初の合成サンプル

WebをCtrl+Cで停止し、アプリのフォルダで次を実行します。既存の資料は変更せず、新しい試験資料を作ります。

```powershell
$env:PYTHONUTF8='1'
& .\.venv\Scripts\python.exe scripts/create_demo.py
if ($LASTEXITCODE) { throw 'サンプル生成失敗。正常と扱わず原因を確認してください' }
$mbDemo = Get-Content -LiteralPath .work\demo-result.json -Raw | ConvertFrom-Json
& .\.venv\Scripts\python.exe -m manga_blendy master $mbDemo.project_id
```

status=succeeded、real_ai=false、reopen_and_inherit=trueを確認します。4方向PNGは640×480、.blendと資料ZIPも生成します。サンプルは通常のBlender生成経路で作り、スクリプト内でZIPの再取込と配置継承を確認します。時間切れや長いパスのエラーは障害対応を確認し、無限再試行しません。

Webを再起動して「開発用：一室の3D連携試験（AI解析なし）」を開きます。4方向の窓・机・家具を目視します。その後、「必要な1コマの背景出力」で基準カメラ、バランス、一段階、320×240を選び、1視点を出力します。こちらはMCP接続の試験です。完成漫画の画質評価とは分けます。

## 8. 保存と再読込み

資料ページのリンクからPNGと編集可能なBlenderファイルを取得できます。一般の資料は以下へ保存します。IDは毎回違うため、画面やJSONの実値を使います。

| 内容 | アプリルートからの場所 |
| --- | --- |
| 元画像・参照 | projects/{project_id}/assets/ |
| 正式.blend・正本JSON | projects/{project_id}/revisions/{revision_id}/ |
| 状態 | projects/{project_id}/states/ |
| 生成PNG・記録 | projects/{project_id}/jobs/{job_id}/output/、renders/ |
| 資料集ZIP | projects/{project_id}/exports/{export_id}/package.zip |
| 端末設定 | config.json |
| サンプル結果・試験ログ | .work/ |

MANGA_BLENDY_HOMEが設定されている場合はそのルートです。Codexはこの変数だけを確認し、勝手に変更しません。新規利用者は未設定のままで構いません。

「資料集をZIPで書き出す」→ホームの「保存した資料集を読み込む」→ZIPを選択。新しい資料IDで表示され、元の資料を上書きしません。ページを閉じてアプリを再起動しても資料が残るか確認します。.blendをBlenderで開くときは、他の未保存作業を保護します。ZIPにはjobsの履歴全体や試験ログは含めず、設定・参照・3D・保存レンダーを移します。

## 9. ChatGPTログイン試験

任意です。モデル一覧の接続試験で、MB本体の完全自動AI機能にはつながりません。GitHubログインとは別です。手順4で確認した基底Pythonから、別の依存を許可後に導入します。

```powershell
& $mbPython -m pip install --target .work\siwc-deps -r scripts\siwc-probe-requirements.lock
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_chatgpt_probe.ps1
```

既存のsiwc-depsは無断更新しません。通常 `http://127.0.0.1:8766/` が開きます。競合時は別ポートになるため、ランチャーの表示URLを使います。本人がContinue with ChatGPTからログインし、名前と権限を確認します。追加クレジットを使わない設定も本人が確認してください。パスワードやトークンをCodexへ渡しません。

「モデル一覧を取得しました」が成功です。画像解析は実行していません。受信サーバーは起動したままなので、期限切れや失敗はページから再ログインします。戻り先は同じ127.0.0.1・実ポートの/auth/callbackです。localhostへ変更しません。試験トークンは保存されず、更新セッションは失効するため、次回も試験用ログインが必要です。終了はCodexがlistener.jsonと/healthのPID・開始時刻を照合して、この試験だけ停止します。

## 10. 自分の画像から資料を作る

初回テストが済んだら本人が画像を選びます。外部AIへ画像を送る許可を確認します。アプリの「新しい資料」の入力形式と指示リンク（/analysis-contract）と画像をCodex/ChatGPTへ渡し、スキーマに従う解析JSONを保存します。元画像のSHA256、見えている事実、推定を分けます。ハッシュの不一致は別画像として拒否されます。

「新しい資料」で画像とJSONを選び、場所、用途、固定する物、時間変化を最初にまとめて指定します。処理後、仮定・未解決点とプレビューを確認し、採用します。読めない画像や復元困難な場面は参照資料のまま保存される場合があります。小物・状態・カメラを別管理し、日付だけで家具や小物を変えません。

## 11. 二段階の漫画仕上げ

資料の1視点出力で二段階を選択します。第1段階が終わったら仕上げZIPを取得し、構造PNG、元画像、編集指示を外部の利用枠内画像編集環境へ渡します。過去の会話を渡す必要はありません。使える画像編集機能がなければ構造画像を保存し、この工程を未実施とします。

できたPNGを候補として取り込み、窓・ドア・家具・遮蔽・接触と、漫画の外観を別々に確認します。承認するまで完成画像としません。画像生成ツールは現在のSign in with ChatGPT経路で未対応です。[公式制限](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)。有料APIや追加クレジットへ切り替えず、枠切れで止めます。

## 12. 確認済み範囲

この端末の既存Python/Blenderでコマンド、合成サンプル、保存・再読込みを確認しています。別PCの新規OS環境、Python 3.14でのアプリ実行、公式ZIPからのBlender新規配置、MCPの新規GUI導入、初回ChatGPT認証は実機未検証です。Codexには各工程の成功・失敗・未実施を分けて報告させてください。

## 13. 更新と問い合わせ

資料集ZIPを保存して、新版は別の空フォルダへ導入してください。古いprojectsやconfig.jsonを無断で消しません。不具合・要望は公開後のGitHub Issuesのフォームで報告できます。原稿や認証情報を貼らず、同梱サンプルで再現してください。
