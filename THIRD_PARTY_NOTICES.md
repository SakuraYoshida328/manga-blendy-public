# ライセンスと第三者表記

v0.1.0-beta.1はソース配布です。Blender、MCPアドオン、Python、依存wheel／DLLは同梱しません。利用者が公式提供元から取得します。2026-10-12に公式資料と、固定版のインストール済み配布物のメタデータ・ライセンス原文を照合しました。

## Manga Blendy

Copyright (c) 2026 Manga Blendy contributors.

本公開候補のソース・文書・合成作例はGPL-3.0-or-later案です。`LICENSE`は[GNU公式のGPL v3原文](https://www.gnu.org/licenses/gpl-3.0.txt)です。公開前に所有者が権利と採用ライセンスを承認します。著作権者名の最終表記も承認対象です。利用者の入力・生成作品のライセンスを自動変更しません。

## Blender（別途導入）

Blender Foundationと各貢献者による自由ソフトウェアです。Blenderバイナリの配布にはGPL-3.0-or-laterが適用されます。`bpy`を使う公開スクリプトはGPLに適合するライセンスが必要とされるため、本候補にもGPL-3.0-or-laterを提案しました。Blender自体や改変バイナリを再配布するパッケージは今回の範囲外です。[公式ライセンス説明](https://www.blender.org/about/license/)、[ソースコード取得](https://www.blender.org/download/source-code/) を参照してください。Blenderで作った作品の権利はソフトのGPLとは区別されます。

## MCP for Blender（別途取得）

Copyright (c) 2025 Siddharth Ahuja — MIT License.

検証コミット `91cd735cc09fc75551de3347ebc7afdd69f3492e`、アドオン表示版1.8、protocol 13。コードは同梱しません。原文を [licenses/mcp-for-blender/LICENSE](licenses/mcp-for-blender/LICENSE) に保管しました。[固定原文](https://github.com/ahujasid/mcp-for-blender/blob/91cd735cc09fc75551de3347ebc7afdd69f3492e/LICENSE) とコードの著作権表記を削除しないでください。別のMCP／フォークへの置換はこの監査対象ではありません。

## Python依存

通常依存31件、任意ログイン試験4件の名前・固定版・ライセンス・原文ハッシュは [DEPENDENCIES.json](DEPENDENCIES.json) です。検証Blender内のrequests関連5配布物も、別スコープで版と原文を記録しています。これは新規導入時の版固定ではありません。著作権者と免責を省略せず、提供されたライセンス・NOTICE・AUTHORSを `licenses/dependencies/` に保存しました。主要ライセンスはMIT、BSD、Apache-2.0、MPL-2.0、PSF-2.0、MIT-CMU等です。複合ライセンスのNumPyとSciPy等は原文を確認してください。上位パッケージのBSD表記だけで内包ライブラリの義務が消えるとは扱いません。

今回は依存の改変・wheel再配布を行いません。将来EXE化や依存バイナリを同梱する場合、OpenSSL／OpenBLAS／GCCランタイム等を含む実際のバンドルを別途監査し、NOTICE、該当ソース提供、ライセンス全文等を満たす必要があります。この一覧をそのままバイナリ版の許可証として使わないでください。

Pythonは[PSF License](https://docs.python.org/3/license.html)、ビルドツールsetuptoolsは[MIT](https://github.com/pypa/setuptools/blob/main/LICENSE)。Python／ビルドツールも同梱しません。新しい版の権利表示は取得した配布物を確認します。

## サービスと素材

OpenAI／ChatGPT／Codexは外部サービスであり、MBのOSSライセンスで権利が付与されるものではありません。本人の契約と[公式接続条件](https://developers.openai.com/siwc/token-sharing-open-source)に従います。BlenderやOpenAIとの提携・公式認定を意味しません。公式ロゴは同梱していません。

公開作例はMBの手定義fixtureからの直接レンダーだけです。私的原稿、私的画像、以前の個人用制作資料、外部アセット、生成AIによる画像は含めません。利用者が取り込む素材は本人が権利を確認してください。
