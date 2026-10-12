# 所有者向け：公開前の確認とリリース手順

この文書は所有者向けのリリース手順です。v0.1.0-beta.1は2026-10-12に公開候補全体の最終承認を受けました。既存Private開発リポジトリを維持し、別Publicリポジトリへ配布します。次版の公開も所有者の承認を必要とします。

## 最終承認で確認すること

1. 新規公開リポジトリの名前。採用名は `manga-blendy-public`。既存のPrivate開発リポジトリは維持する。
2. GPL-3.0-or-later案、著作権表記 `Manga Blendy contributors`、コード・文書・合成作例を公開する権利。第三者の原文表記を削除しない。
3. READMEに記載した機能境界、対応環境、実機未検証、合成作例による紹介でよいこと。
4. 配布ZIPとSHA256、PUBLIC_BETA_AUDIT.md、SOURCE_MANIFEST.jsonの確認。

## 承認後だけ行う作業

- GitHubで新規Publicリポジトリを作る。既存Privateリポジトリの公開設定を変更しない。自動README／LICENSE／.gitignoreは生成せず、独立した公開候補の履歴を使う。
- READMEの候補・承認待ち表記を公開済みの事実へ更新し、実際の公開URLとReleasesへのリンクを記載する。変更をレビューし、manifest・ZIP・SHAを作り直す。Private側の履歴をmerge／pushしない。
- この公開候補のGitルートとmain、追跡ファイル、remote未設定を確認してから、**作成した実リポジトリURL**をoriginへ設定する。

```powershell
git status --short
git branch --show-current
git log --oneline
git remote -v
$mbPublicRemote = Read-Host '承認後に作成した公開リポジトリのHTTPS URL'
git remote add origin $mbPublicRemote
git push -u origin main
```

push認証が必要ならGit Credential Managerのブラウザで所有者本人が確認する。トークンをCodexへ送らない。既存originがある場合は先に確認し、勝手に置換しない。force pushを使わない。

- 公開用のmainを確認し、最終候補コミットに `v0.1.0-beta.1` タグを付けてpushする。既存同名タグは上書きしない。認証済みの所有者のGit identityを使う。
- GitHub ReleasesでこのタグのReleaseを作り、**Pre-release**をON。RELEASE_NOTES.mdと実機未検証を説明に載せ、監査した配布ZIP・SHA256SUMS.txt・SOURCE_MANIFEST.jsonを添付する。
- IssuesをON。不具合／機能要望フォームを確認。Private vulnerability reportingの設定を確認し、SECURITY.mdの窓口が利用できるか確認する。
- ログアウトしたブラウザでREADME、導入説明、ReleasesのZIP・SHA、Issuesフォームを確認する。匿名で取得できることとアップロード後のSHA一致を検証する。一般公開であって招待制にしない。
- LINEへの投稿は所有者が行う。公開URLを示し、自動AI解析・外部漫画仕上げ・実機未検証の制限を省かない。AIからグループへ自動送信しない。

## 次版の維持

Private開発の最新ファイルから、公開済みファイルの範囲だけを新しい候補へ反映する。私的projects・experiments・設定・ログ・画像や古いGitオブジェクトをコピーしない。今後も新しい公開コミット／タグを追加し、既存公開履歴を消さない。

依存の変更やEXE化は別のライセンス監査が必要。既存資料の互換、配置、保存3D、二段階承認を検証し、利用者には資料ZIPを保持した別フォルダ更新を案内する。
