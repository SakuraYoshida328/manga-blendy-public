# CodexによるManga Blendyベータ版導入

この文書は、新しい会話のCodexがリポジトリと本書だけで導入を支援するための作業手順です。更新日2026-10-12。人間向け説明は [getting-started.md](getting-started.md)、障害対応は [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

## 0. 作業契約

- 最初に読み取りで環境・保存先・既存導入を確認する。会話履歴、個人のメモリ、既存キャッシュを必須にしない。
- 不足ソフトの導入、依存の取得、プロセス限りの実行ポリシー、アドオン導入・設定保存、サンプル作成の前に、対象・保存先・変更範囲をまとめて利用者へ示し、許可を得る。承認済み範囲内の各ステップで質問を繰り返さない。
- インストールだけでなく初回テストまで進める。許可されない工程は飛ばさず「未実施」と記録する。追加の重要変更は再確認する。
- パスワード、APIキー、OAuthコード・トークンを要求・表示しない。本人認証はブラウザで利用者が行う。全環境変数、credential fillの出力、認証URLのクエリをログへ出さない。
- 既存のBlender・MCP・Codex設定、既存.venv、ユーザーデータを無断で上書きしない。既存作業フォルダはgit statusを確認してから再利用する。導入作業でGitHub公開設定や履歴を書き換えず、有料API・外部アセットを使わない。
- 読取りの確認以外は利用者が承認した作業フォルダ内へ保存する。OSの長いパス設定、TLS検証、ファイアウォール、恒久的実行ポリシーを勝手に変更しない。

成功の基本条件は「アプリ起動・Blender/MCP診断・合成サンプル生成・PNG/.blend保存・資料ZIP再読込み」。ChatGPTモデル一覧試験は任意の別判定。完全自動AI解析・漫画仕上げは現行の成功条件に含めない。

## 1. 環境の読取り確認

Windows 11 x64、PowerShell 7をベータの基本構成とする。別PCの新規導入は実機未検証。Windows 10、ARM64、他のPython/Blender版は成功を仮定しない。

```powershell
$PSVersionTable.PSVersion
[System.Runtime.InteropServices.RuntimeInformation]::OSDescription
[System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
Get-Command pwsh,git,python,py -ErrorAction SilentlyContinue | Select-Object Name,Source
```

Gitがある場合は `git --version` と `git credential-manager --version`。Python候補は `py -0p`（pyがある場合）またはGet-Commandの実値を確認する。WindowsAppsの実行エイリアスを本物のPythonと決めつけない。Codex同梱Pythonは setup.ps1 が検出することがあるが、そのパスの存在・版・pipを確認できた場合だけ使う。

確認した実行ファイルを `$mbPython`、既存Blender 5.2を `$mbBlender` に入れる。入力は実機の実値であり、開発者のユーザー名やフォルダを使わない。

```powershell
# 確認した実値を入力する。対話入力が使えないCodexは利用者の許可済み実値を変数へ代入する。
$mbPython = Read-Host '確認したPython実行ファイルのフルパス'
if (-not (Test-Path -LiteralPath $mbPython -PathType Leaf)) { throw 'Pythonの実行ファイルを確認してください' }
& $mbPython -c "import sys,platform;print(sys.version);print(platform.machine());assert sys.version_info >= (3,12)"
if ($LASTEXITCODE) { throw 'Python 3.12以上が必要です' }
& $mbPython -m pip --version
```

pipは `--python` 対応の22.3以上が必要。未導入なら許可後にそのPythonの `-m ensurepip --upgrade`、古い場合は許可後に `-m pip install --upgrade pip`。共有Pythonの変更を避ける場合は別の導入を提案する。

不足ソフトは利用者の許可後に公式配布から導入する： [Python Windows](https://www.python.org/downloads/windows/)、[PowerShell 7](https://learn.microsoft.com/en-us/powershell/scripting/install/install-powershell-on-windows)、[Git for Windows](https://git-scm.com/install/windows)。インストーラーの署名・対象アーキテクチャと保存先を確認する。管理者実行を既定にしない。新規Python候補は3.14 x64。固定依存の解決は確認済み、アプリ実行は実機未検証。3.12.14の公式Windowsインストーラーは存在しないので架空のURLを作らない。

PowerShell 7を用意したらそのターミナルへ切り替える。以下のコマンドはPowerShell 7用。公開候補のPS1はUTF-8 BOM付きです。PowerShell 7を推奨し、標準5.1もこの候補の構文・起動を検査します。旧版のBOMなしrun.ps1とは区別します。

## 2. 公開ベータの取得

所有者の最終承認までは未公開です。公開後は、利用者が渡した実在する公開リポジトリURLを使います。Private開発リポジトリへ案内しません。招待・GitHub認証は不要です。取得が404ならURLと公開状況を確認して停止し、認証情報を要求しません。

推奨はそのリポジトリのReleases → v0.1.0-beta.1 → `manga-blendy-v0.1.0-beta.1-windows-source.zip` と `SHA256SUMS.txt`。同じReleaseから取得したチェックサムと照合し、許可した新しい空の短いフォルダへ展開します。ソース形式なのでGitをインストールしなくても使えます。

```powershell
# 利用者が取得した実ファイルを入力。架空のダウンロードURLを作らない。
$mbZip = Read-Host '取得した配布ZIPのフルパス'
$mbSums = Read-Host '同じReleaseのSHA256SUMS.txtのフルパス'
$mbName = Split-Path $mbZip -Leaf
$mbEntries = @(Get-Content -LiteralPath $mbSums | Where-Object { $_ -match ('^[0-9a-fA-F]{64}  ' + [regex]::Escape($mbName) + '$') })
if ($mbEntries.Count -ne 1) { throw '対応するチェックサムがありません' }
$mbExpected = $mbEntries[0].Substring(0,64)
if ((Get-FileHash -LiteralPath $mbZip -Algorithm SHA256).Hash -ne $mbExpected) { throw 'ZIPのハッシュが一致しません' }
$mbExtract = Read-Host '新しい空の展開先'
if (Test-Path -LiteralPath $mbExtract) { throw '既存フォルダを上書きしません' }
Expand-Archive -LiteralPath $mbZip -DestinationPath $mbExtract
$mbRoot = Join-Path $mbExtract 'manga-blendy'
if (-not (Test-Path -LiteralPath (Join-Path $mbRoot 'setup.ps1'))) { throw 'MBのルートを確認してください' }
Set-Location -LiteralPath $mbRoot
```

Gitが既にある場合はcloneも選べます。手元の公開URLを使い、既存保存先を再利用しません。

```powershell
$mbRepo = Read-Host '利用者が渡した公開リポジトリのHTTPS URL'
$mbRoot = Read-Host '新しい短いローカル保存先'
if (Test-Path -LiteralPath $mbRoot) { throw '既存フォルダを上書きしません' }
git clone $mbRepo $mbRoot
if ($LASTEXITCODE) { throw 'URL・公開状況・通信を確認してください' }
Set-Location -LiteralPath $mbRoot
git status --short
git remote get-url origin
git rev-parse HEAD
```

ルートは60文字程度までを目安にします。長いパス、クラウド同期フォルダ、Program Filesを避けます。ZIPの中の`SOURCE_MANIFEST.json`は公開候補のファイルハッシュです。既存.venvがある場所ではセットアップせず、別の空フォルダを選びます。

GitHubのCode → Download ZIPは、Releaseの配布ZIPとはファイル名・フォルダ名が違います。その場合はREADMEとsetup.ps1を持つ実フォルダをルートにし、Release専用チェックサムを流用しません。将来Private版を明示指定された場合だけ所有者の閲覧権限とGCMの本人ブラウザ認証を確認します。一般公開ベータでは不要です。

## 3. Python依存とMBセットアップ

アプリのルートで、確認した `$mbPython` を使う。インストール・プロセス限りのBypassは工程0で許可を得る。組織のGroup Policyが拒否する場合は止め、IT担当へ相談する。

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -PythonPath $mbPython
if ($LASTEXITCODE) { throw 'セットアップに失敗しました。以降の生成を実行しません' }
& .\.venv\Scripts\python.exe -c "import manga_blendy,fastapi,PIL,numpy,scipy;print('MB_IMPORT_OK')"
if ($LASTEXITCODE) { throw '依存の読込みに失敗しました' }
```

setup.ps1は `.venv` をpipなしで作り、**基底Pythonのpip**からrequirements.lockと編集可能なMBパッケージを導入する。`.venv\Scripts\python.exe -m pip` が使えないのはこの方式では正常。requirements.lockにはpytestも含む。pipの版を勝手に緩めたり、依存名を推定して追加したりしない。

## 4. Blender 5.2の確認・導入

既存の5.2を優先する。実行ファイルはエクスプローラー等で実値を確認し、次を実行する。

```powershell
$mbBlender = Read-Host '確認したBlender 5.2実行ファイルのフルパス'
& $mbBlender --version
```

先頭が `Blender 5.2` であることを確認。未導入なら許可を得て [公式5.2配布ディレクトリ](https://download.blender.org/release/Blender5.2/) のWindows x64版を選ぶ。検証した実行版は5.2.0 LTS。公式に `blender-5.2.0-windows-x64.zip` とMSIが存在する。既存Blenderを上書きしないZIP配置が選べる。別PCでの新規配置・UI起動は実機未検証。

ZIPを使う場合は、利用者の許可した新しい空の配置先へダウンロード・展開し、公式チェックサムと照合する。既存の展開先を使い回さない。展開後に `Get-ChildItem -LiteralPath <確認した展開先> -Filter blender.exe -Recurse` で実行ファイルを探し、複数なら選択を確認する。`C:\Program Files\Blender Foundation\Blender 5.2\blender.exe` はアプリの既定値であり、存在を確認せず採用しない。

## 5. 検証済みBlender側MCPアドオン

MBはBlender側のローカルコマンドサーバーに直接接続する。MBの実行にuv、MCPクライアント用サーバー、Codexの設定への登録は不要。既存の動くアドオンは読み取り診断を先に行い、勝手に更新しない。

固定する配布物：第三者のMCP for Blender、表示版1.8、protocol 13。

- コミット：`91cd735cc09fc75551de3347ebc7afdd69f3492e`
- [提供元コード](https://github.com/ahujasid/mcp-for-blender/blob/91cd735cc09fc75551de3347ebc7afdd69f3492e/addon.py)
- SHA256：`eb0facf69781a30e69792532087d8d41c6a14fcd323353250abe7988ee297fa5`

このSHAはこの端末の既存検証アドオンと、固定コミットから取得したバイト列の一致を確認したもの。第三者コードは同梱せず、利用者の許可後に取得する。新規の別PCへのインストールは実機未検証。

```powershell
New-Item -ItemType Directory -Force -Path .work\install | Out-Null
$mbAddon = Join-Path (Get-Location).Path '.work\install\blender_mcp.py'
if (Test-Path -LiteralPath $mbAddon) { throw '既存の取得ファイルを確認してください。上書きしません' }
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/ahujasid/mcp-for-blender/91cd735cc09fc75551de3347ebc7afdd69f3492e/addon.py' -OutFile $mbAddon
if ((Get-FileHash -LiteralPath $mbAddon -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'eb0facf69781a30e69792532087d8d41c6a14fcd323353250abe7988ee297fa5') { throw 'アドオンのハッシュが一致しません。導入を中止します' }
```

Blenderを開き、編集 → プリファレンス → アドオン → メニューのInstall from Diskからこの `.py` を選び、有効化する。既存blender_mcpモジュールがあるなら、先に版・ハッシュと設定を確認し、入替えは別途許可を得る。保存前に既存の他アドオンや設定が変わらないことを確認する。CodexにGUI操作手段がなければこの操作だけ利用者へ依頼する。

アドオンはBlenderのPythonの `requests` をimportする。ImportErrorなら無理に有効化せずTROUBLESHOOTING.mdに従う。外部アセット・有料連携（Poly Haven、Sketchfab、Hyper3D、Hunyuan3D等）はOFF、Allow TelemetryもOFFを確認する。既存の設定変更は許可を得る。

3DビューでN → MCP for Blender。ポート9876、Connect to MCP server。Connected on port 9876なら成功候補。既に接続済みなら重複起動しない。新版をlatestという理由で使わない。この版は自動開始が既定なので、作業後はDisconnectし、必要なときだけBlenderを使う。MBはBlenderを常駐起動しない。

## 6. アプリ起動・設定・診断

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

別ターミナルまたはブラウザで `http://127.0.0.1:8765/setup` を開く。Blenderパスと利用方式を保存する操作は許可済み範囲で行う。方式はChatGPT利用枠：外部解析・編集して取込。API方式は無効。

接続確認でBlenderとMCPが両方ready=true。これは読み取りで、モデル変更やAI解析をしない。CLIでも同じ診断を実行できる。

```powershell
& .\.venv\Scripts\python.exe -m manga_blendy diagnose
```

診断のJSONを確認する。CLI終了コード0だけで成功と判定しない。`blender.ready` と `mcp.ready` の両方を見る。Webは8765、MCPはIPv4の127.0.0.1:9876。ポート競合時は他プロセスを終了せずTROUBLESHOOTING.mdへ。

## 7. 合成サンプルの初回生成・再読込み

最初の試験は既存の `scripts/create_demo.py` と `tests/fixtures.py` の手定義一室を使う。私的な原稿・画像・過去の資料・AIサービスは不要。これは空間と保存の試験であり、漫画の完成品質の試験ではない。

WebのターミナルをCtrl+Cで閉じてから実行する。別のワーカーと同時実行しない。

```powershell
$env:PYTHONUTF8='1'
& .\.venv\Scripts\python.exe scripts/create_demo.py
if ($LASTEXITCODE) { throw 'サンプル試験に失敗しました。正常と扱わず原因を確認します' }
$mbDemo = Get-Content -LiteralPath .work\demo-result.json -Raw | ConvertFrom-Json
& .\.venv\Scripts\python.exe -m manga_blendy list
& .\.venv\Scripts\python.exe -m manga_blendy master $mbDemo.project_id
```

成功条件：JSONにstatus=succeeded、real_ai=false、reopen_and_inherit=true。4方向PNGが640×480、`.blend` が存在。scriptはZIPを書き出し、独立領域へ取込んで配置継承も確認する。再試行は原因を確認し、無限に生成しない。生成は初回確認用の4方向だけ。

run.ps1でWebを再起動し、「開発用：一室の3D連携試験（AI解析なし）」を開く。4画像が表示され、机・窓・家具が同じ空間として成立しているか目視する。「資料集をZIPで書き出す」→ホームの「保存した資料集を読み込む」→そのZIPを選ぶ。新しいIDで取り込まれ、元資料を上書きしないことを確認する。Blenderファイルもアプリのリンクから取得・再オープンする。未保存の別Blender作業を閉じない。

MCP経路は資料ページの「必要な1コマの背景出力」でカメラ基準、バランス、一段階、幅320・高さ240を指定し、「この設定で1視点を出力」。1枚の白黒PNGが生成され、エラーなく配置を保つことを確認する。この工程にはMCP接続が必要。

## 8. ChatGPTログイン試験（任意・別判定）

合成サンプルには不要。本人がPlus等の利用枠と権限を確認するための、モデル一覧までの開発試験。これを本体自動AI機能の有効化と扱わない。

通常のrequirements.lockとは別の依存がある。許可後、工程1の基底Pythonを使って導入する。

```powershell
& $mbPython -m pip install --target .work\siwc-deps -r scripts\siwc-probe-requirements.lock
if ($LASTEXITCODE) { throw 'ログイン試験の依存導入に失敗しました' }
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_chatgpt_probe.ps1
```

既存siwc-depsがある場合、勝手に更新せず版を確認する。ランチャーは受信サーバーを独立起動し、ブラウザを開く。通常URLは `http://127.0.0.1:8766/`。競合時は空きポートになるため、表示された実URLを使う。リダイレクトは同じIPv4・ポートの `/auth/callback`。localhostへ書き換えない。

本人がContinue with ChatGPTを押し、OpenAIの画面でManga Blendyの名前と権限を確認する。ChatGPT側の利用設定で追加クレジットを使わないことを本人が確認する。サーバーは認証中も結果表示後も終了しない。

成功は「モデル一覧を取得しました」と、`.work/siwc-probe/result.json` のstatus=model_list_succeeded、inference_requests=0、tokens_written_to_disk=false。モデルの件数・IDを固定しない。失敗時は診断コードで判断し、トークンやcallback URLを表示しない。同じボタンから再ログイン可能。試験後に更新セッションを失効させ、トークンを保存しないため、次回も試験用ログインを行う。

終了時は `.work/siwc-probe/listener.json` のservice=manga-blendy-siwc-probe、PID、開始時刻と、そのURLの `/health` の同じservice/PIDを照合し、試験用プロセスだけを利用者の許可済み範囲で停止する。他のPythonを一括終了しない。

## 9. 実画像と二段階仕上げの基本操作

実画像は利用者が選ぶ。外部AIへ画像を渡すことも本人の許可を確認する。アプリの `/analysis-contract` に現行JSONスキーマと固定指示がある。元画像のハッシュは `(Get-FileHash -LiteralPath <本人が選んだ画像> -Algorithm SHA256).Hash.ToLowerInvariant()`。

Codex/ChatGPTへ元画像と契約を渡し、トップレベルcontract=manga-blendy.observation、schema_version=1、source_sha256、observationを持つJSONを生成してもらう。見える事実と推定を分け、観測形式はスキーマに従う。AIが読めない場合は停止し、ダミー解析を実画像の事実として使わない。JSON保存後 `/new` で元画像とJSON、最初の条件をまとめて入力する。結果の仮定・プレビューを見て採用する。入力画像を後から編集するとハッシュが変わる。

二段階を選ぶ場合、1視点の構造PNGを撮影後、仕上げ画面からZIPを取得。stage1.pngを構図、style_reference.pngを外観、request.jsonとedit_prompt.txtを指示として、利用枠内で使える外部画像編集環境へ渡す。利用枠切れでは停止する。有料APIへの切替はしない。編集PNGを候補取込し、構造の全項目と漫画外観を別々に確認・承認。未承認は完成出力にしない。Sign in with ChatGPTのこの経路は画像生成ツール未対応：[公式制限](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)。

## 10. 保存場所・完了報告

既定はアプリのルート。MANGA_BLENDY_HOMEが設定済みならconfig.pyのそのルートを使うため、最初に当該変数だけ確認し、勝手に変更しない。

| ファイル | アプリルートからの場所 |
| --- | --- |
| 端末設定 | config.json（Git対象外） |
| 元画像・詳細参照 | projects/{project_id}/assets/ |
| 正式3D・正本JSON | projects/{project_id}/revisions/{revision_id}/lightweight.blend、scene.json |
| 場面状態 | projects/{project_id}/states/{state_id}.json |
| PNG・結果 | projects/{project_id}/jobs/{job_id}/output/ と renders/{revision_id}/{state_id}/{hash}/ |
| 資料集ZIP | projects/{project_id}/exports/{export_id}/package.zip |
| 外部アプリ用PNG＋JSON | projects/{project_id}/exports/images/{hash}/background.png、background.json |
| 合成サンプル結果 | .work/demo-result.json。実際のIDとexportパスをここから取得 |
| 試験用ログ・認証診断 | .work/（共有・Gitへ送らない） |

完了報告は、ソフトの実値と版、取得コミット、保存先、承認した変更、Web/Blender/MCP/サンプル/ZIP再読込み/任意ログインの個別結果、未実施、実機未検証、エラーと対処をまとめる。秘匿値は記載しない。別PCで実行して初めてそのPCの導入成功と判定する。

## 11. 本書の再監査と検証範囲

2026-10-12、前の会話を前提にせず、取得→必要環境→許可→セットアップ→Blender/MCP→合成生成→保存→再読込み→任意ログイン→外部AI取込の順で再監査した。脚注や開発者固有のファイルを手順の前提にしない。

この公開候補は独立した空の.venvからsetup.ps1を実行し、固定31依存の導入、通常テスト72件、認証オフライン15件、合成4方向・.blend・ZIP再取込、保存3DのMCP320×240を確認した。PowerShell 7と標準5.1で3本のPS1と19個の文書コードブロックの構文エラー0、標準5.1のWeb6画面と認証受信ランチャーの起動も確認した。ローカル文書リンク35件を確認。処理ロジックは変更せず、候補だけPS1のBOM追加とバージョン表記を揃えた。今回の実ChatGPT認証とAI推論は未実施。配布ZIPの検証結果はPUBLIC_BETA_AUDIT.mdとaudit-data.jsonを参照する。

**実機未検証**：別PCの空環境、Python 3.14での実行、Python/Git/PowerShellの新規インストール、Blender公式ZIPからの新規配置、MCPの新規GUI導入、別PCの初回ChatGPT認証。新しい生成AI画像も今回作っていない。これらをこの文書の完成だけで成功扱いしない。

### 参照した実装

| 手順 | リポジトリ内の根拠 |
| --- | --- |
| venv・pip・起動・Port | setup.ps1、run.ps1、requirements.lock、pyproject.toml |
| 診断・読み取り・利用方式 | src/manga_blendy/diagnostics.py、config.py、mcp_transport.py |
| 合成サンプル | scripts/create_demo.py、tests/fixtures.py |
| 保存・移行 | src/manga_blendy/storage.py、integration.py |
| ログインの依存・ポート・寿命 | scripts/siwc-probe-requirements.lock、start_chatgpt_probe.ps1、siwc_probe.py |
| 実画像・1視点・承認 | src/manga_blendy/observation_import.py、manga_style.py、finishing.py、web/templates/ |

## 12. 一般公開ベータでの注意

- 本体AI未実装・外部作業の境界はREADMEとAI-capabilities.mdを先に説明する。Plus契約で全工程が自動化されると説明しない。本人の契約・地域・組織制限でログインできない場合はその工程を未実施にする。
- 初回試験に私的原稿は使わない。バグ報告は公開Issueテンプレートで、同梱サンプルの再現と最小の秘匿値を除いたエラーに限定する。
- MCPはローカルPython実行権限を持つ。外部ネットワークへポートを開放せず、必要時のみ接続。アドオンの更新通知があっても検証コミットを自動更新しない。
- アドオン新規導入時のrequests不足は、Blender Pythonの実値・版・ensurepipの有無を読取り確認する。必要なら利用者の許可後、独立したBlender ZIP配置のPythonだけへ `-m ensurepip` と `-m pip install requests` を実行する。共有Blenderへ無断で導入しない。実際の依存版・ライセンスを記録する。対象が確定しなければ停止し、実機未検証と報告する。
- パッケージの版と検証結果はPUBLIC_BETA_AUDIT.mdを参照する。新しいPCで成功した項目だけ、そのPCの成功として報告する。
