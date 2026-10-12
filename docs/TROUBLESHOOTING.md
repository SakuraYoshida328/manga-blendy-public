# Manga Blendy ベータ版：エラーと対処

更新日2026-10-12。まず停止した工程とエラーコードを確認します。ログには原稿・個人情報が入る場合があるため、認証URL、トークン、Cookie、全環境変数、原稿を含むログ全体をチャットやGitHubへ貼りません。最小のエラー行だけを使います。

## 取得・環境・セットアップ

| 症状 | 原因確認と対処 |
| --- | --- |
| GitHubで404／Repository not found | 公開URLとReleaseの公開状況、URLの綴りを確認。接続エラーや取得直後の一時的な反映遅延も確認。公開版に招待は不要。明示指定されたPrivate版だけ本人の権限を確認 |
| Git認証失敗 | 公開版は認証不要。公開URLを確認する。Private版を明示指定された場合だけGCMのブラウザ本人認証を使い、パスワード・PATをチャットへ貼らない |
| gitが見つからない | 許可後にGit公式から導入するか、公開ReleasesからZIP取得 |
| pythonがStoreを開く／pyがない | 実行エイリアスを使わず、実際のPython.exeを確認。必要なら公式Pythonの導入を提案 |
| Python 3.12.14のWindowsインストーラーがない | この版はソース配布のみ。既存の動くPythonを再利用するか、公式の新しいWindows x64版を選ぶ。架空のURLを作らない |
| No module named pip（基底Python） | 許可後にそのPythonの`-m ensurepip --upgrade`。同梱がなければpip付き公式配布を提案 |
| No module named pip（.venv） | setup.ps1の方式では正常。依存導入には確認した基底Pythonのpipを使う |
| pipの--pythonが認識されない | 基底pipは22.3以上が必要。共有Pythonを勝手に更新せず、更新または別環境を提案して許可を得る |
| run.ps1の文字化け／文字列・構文エラー | 旧BOMなしファイルをPowerShell 5.1で読んだ場合に確認済み。公開候補はBOM付きに修正。PowerShell 7の`pwsh -NoProfile -ExecutionPolicy Bypass -File .\run.ps1`を使う。start.cmdは5.1を呼ぶ |
| scripts disabled／実行禁止 | 利用者の許可後にPowerShell 7のプロセス限りのBypassで実行。Group Policyや組織制限は回避せずIT担当へ。恒久的Set-ExecutionPolicyを行わない |
| requirements.lockの導入失敗 | 基底Pythonの版、x64、pip、通信、プロキシ、空き容量を確認。固定版を無断で緩めない。2026-10-12に固定31パッケージのPyPI存在と3.14 x64依存解決を確認したが、そのPCの導入成功は別判定 |
| SSL／証明書エラー | PC時刻、公式URL、組織のCA・プロキシを確認し、必要ならIT担当へ。TLS検証を無効化しない |
| 既存.venv／既存フォルダ | git statusと利用中の環境を確認。消去・上書きせず、別の空フォルダへ導入する案を提示 |
| No module named manga_blendy | setup.ps1の終了コードとMB_IMPORT_OKを確認。別Pythonで起動していないか確認。再導入は対象.venvへの許可済み操作だけ |
| ZIP再読込みが破損扱い・FileNotFoundErrorで長いパス | この端末の長い試験保存先で確認済み。元ZIPを保持し、短いローカル保存先で再確認する。OSのLongPathsEnabledを勝手に変えない |

## WebとBlender・MCP

Webの既定ポートは8765です。起動ターミナルを閉じるとアプリも終了します。ポート競合は他プロセスを勝手に止めず、許可した別ポートで次を使えます。

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Port 8767
```

その場合ブラウザも `http://127.0.0.1:8767/` にします。API・契約のURLも同じ実ポートです。MCPの9876を8767に変えることはありません。

| コード・症状 | 対処 |
| --- | --- |
| BLENDER_NOT_FOUND | 実際のblender.exeを確認し、/setupへ入力・保存。既定Program Filesパスを存在確認せず使わない |
| BLENDER_VERSION | 5.2を指定。他版を消さず、必要なら承認した別配置を使用 |
| BLENDER_FAILED／BLENDER_TIMEOUT | 作業用ログの最小エラー、版、権限、メモリを確認。大量再試行や別人のプロセス終了をしない |
| MCP_UNAVAILABLE | Blender側アドオンを有効にし、NパネルのMCP for Blenderで9876を開始。IPv4の127.0.0.1へ届くか診断 |
| MCP_FAILED／応答不正 | 固定コミット・SHA256とget_scene_info/execute_codeの互換を確認。最新版へ自動更新しない |
| Addon hash mismatch | 固定URLとSHAを確認して停止。異なる内容を導入しない |
| アドオンのimport requestsが失敗 | BlenderのPythonとアドオン依存の問題。一般のPythonにrequestsを入れてもBlenderには届かない。Blenderのコンソールでsys.executable等の実値を確認し、追加導入の対象と影響を説明して許可を得る。実値不明のまま推定したPythonへpipを実行しない。別PCでの補助導入は実機未検証 |
| MCP already running／9876競合 | 同じBlenderの接続を再利用できるか確認。別Blenderを一括終了しない |
| SAVED_GEOMETRY_REQUIRED | その資料の保存3Dが必要。合成サンプル生成・採用、または準備済み資料ZIPを利用。グレー模型を漫画完成として代用しない |
| OUTPUT_INVALID | 正本・状態・保存3D・PNGのハッシュ／配置の不一致。原本を保持して原因を確認し、不一致を無視しない |
| 1視点出力が止まる | まず作業状態とMCPの現在の1枚を確認。キャンセルは撮影完了とシーン復元を待つ方式。所有していないBlenderを終了しない |
| 合成サンプルが時間切れ | Webや別JobManagerと同時実行していないか、CPU負荷・短い保存先を確認。完了前のジョブを成功扱いしない。作業記録のPIDと開始時刻を照合して自分の試験だけ停止。新規再試行の前に原因を確認 |

## ChatGPTログイン試験

本体は/8765、ログイン試験は通常/8766の別サーバーです。MCPとは別です。試験はモデル一覧までで、推論や画像生成を送りません。

| 症状・コード | 対処 |
| --- | --- |
| ModuleNotFoundError: jwt／cryptography | 基底Pythonのpipでscripts/siwc-probe-requirements.lockを.work/siwc-depsへ導入。既存版は無断で上書きしない |
| ERR_CONNECTION_REFUSED | PowerShell 7でscripts/start_chatgpt_probe.ps1を再実行。表示された実URLへ接続。チャット側の短命な実行セルだけにサーバーを依存させない |
| localhostと127.0.0.1の不一致 | 表示URLの127.0.0.1をそのまま使用。認証開始とcallbackは同じブラウザ・IPv4・ポート |
| 8766が使用中 | ランチャーが選んだ空きポートとlistener.jsonのURLを使う。他プロセスを止めない |
| authorization_expired | ページから新しくログイン。受信サーバーは続いている |
| 古い認証結果／callback_state_mismatch | 古いタブのcallbackを再利用せず、同じ試験ページのContinue with ChatGPTからやり直す |
| scope／本人検証の失敗 | 診断コードと本人が同意した権限を確認。有料APIへ変えず、勝手に検証を省略しない |
| model_list_succeeded | ログイン・モデル一覧試験の成功。本体AI解析・画像生成の成功ではない |
| 429／利用枠上限 | 外部AI工程を停止。追加クレジットやAPIキーへ切り替えない |

通常の `ChatGPTログイン.cmd` はWindows PowerShellを起動します。ベータの推奨はPowerShell 7からstart_chatgpt_probe.ps1を実行する方法です。

ブラウザを閉じても受信サーバーは終了しません。停止するときは、listener.jsonのservice/PID/開始時刻と、実URLの/healthのservice/PIDが一致することを確認します。PIDを見ただけでStop-Processしないでください。認証コード・Cookie・Authorizationヘッダー・トークン・callbackの完全URLをログに保存しません。

## 実画像・二段階・移行

| コード・症状 | 対処 |
| --- | --- |
| SOURCE_MISMATCH | 選んだ元ファイルのSHA256と解析JSONを照合。リサイズ・再保存後の画像へ旧JSONを流用しない |
| INPUT_INVALID（観測） | /analysis-contractのスキーマ、JSONのUTF-8、サイズを確認。元画像20MB以下、観測JSON2MB以下 |
| reference_only | 空間復元の条件不足。参照保存を確認し、3D完成と扱わない |
| awaiting_external_edit | 第1段階完了、外部の漫画編集待ち。handoff.zipを取得。ログインし直せば自動編集されるわけではない |
| FINISH_REVIEW_REQUIRED | 候補画像を取り込み、構造の全項目と外観を人が確認して承認する |
| 移行ZIPが拒否される | MBの「資料集ZIP」を使う。.blend単体や旧実験JSONを標準ZIPと混同しない。受信サイズ500MB、展開総量1GBの制限がある。原本保持で欠損・ハッシュを確認 |

まだ別PCで確認していない問題を推測で解決済みにしません。報告にはPC構成、取得コミット、失敗工程、秘匿値を除いたエラー、対処、未実施を記載してください。

## 公開パッケージ

チェックサム不一致は展開・実行を停止し、同じReleaseのZIPとSHA256SUMSを取り直してください。Codeの自動生成ZIPにRelease用SHAを流用しません。配布ZIPはソース形式で、exeがないのは正常です。PUBLIC_BETA_AUDIT.mdにその版の実機未検証項目があります。
