# Muse <-> ChatGPT Bridge

## 調査結果（2026-10-25）

### danny-hines/muse-code-bridge — 実在、MIT、利用不可（本件の目的には）

- ChatGPT デスクトップ / Codex から Muse Code を呼ぶための MCP ツールとスキル集。macOS でテスト済み。
- Hermes 連携は「作業中・未テスト」と明記されており、Linux VM では動作しない（Muse Code 1.0.3+ と Codex CLI が前提で、この VM には Muse Code 実行ファイルがない）。
- ホスト型リレーや認証情報は含まない。設計（MCP ツール、セッション継続、承認リレー）のみ参考にし、コードはコピーしていない。

### bex-co/muse-code-acp — 実在、Apache-2.0、利用不可（本件の目的には）

- Muse Code を Agent Client Protocol（ACP）で Zed / VS Code に接続するアダプタ。ChatGPT でも iPhone でもない。
- こちらも Muse Code 実行ファイルが別途必要で、この VM にはない。コードはコピーしていない。
- なお Hermes 本体には既に `acp_adapter/`（ACP サーバー）が存在するため、ACP 方向の重複実装は避け、本ブリッジは ChatGPT 向け MCP だけを担う。

### ChatGPT のカスタム MCP 接続とプラグイン（2026-10-25 時点の公式ドキュメントまとめ）

- カスタム MCP サーバーの**追加（作成）は Web 版**で行う。作成したものはプラグインとして扱われ、公式プラグインドキュメントによれば、プラグインは Web・デスクトップ・モバイルで利用でき、モバイルでは自分のアカウントで利用可能なプラグインを使える。ただし「Desktop only」と表示されるプラグインはモバイルでは使えない。
- つまり手順は「Web でプラグインを作成・インストール → iPhone アプリで同じアカウントのチャットから使う」であり、iPhone アプリ内からサーバーを追加する操作は不要（かつ存在しない）。
- フルな読み書き対応は Business / Enterprise / Edu（ベータ）に限られ、Pro は読み取り・取得のみとされる資料がある。Plus / Free の扱いは資料によって異なり、自分のアカウントとワークスペース設定で確認が必要。書き込みツールは既定で確認が求められ、会話ごとに記憶できる。
- 認証は OAuth または認証なしで、Bearer ヘッダーを送る選択肢は ChatGPT の接続画面にない。したがって本ブリッジは OAuth 2.1（`muse_bridge/oauth.py`）を実装した：RFC 9728 の保護リソースメタデータ、RFC 8414 の認可サーバーメタデータ、動的クライアント登録、認可コード＋PKCE S256、リフレッシュトークンのローテーション、失効。Bearer トークンは汎用 MCP クライアント（Claude / Codex / curl）向けに残してある。
- 公開 HTTPS エンドポイントを持たない場合の代替は Secure MCP Tunnel：トンネルクライアントがこのサーバーの loopback へ転送し、ChatGPT はトンネル経由で接続する（公開プラグインとしての配布には対応しないが、個人利用には十分）。公開 URL は本件では発行していない（下記参照）。

## 実装（`muse_bridge/`、依存なし・Python 標準ライブラリのみ）

- `server.py`：`POST /mcp`（JSON-RPC：initialize / tools/list / tools/call / ping）と `GET /health`。ツールは `muse_send_instruction` / `muse_get_status` / `muse_get_result` / `muse_list_sessions` / `muse_get_session` / `muse_run_tests` / `muse_get_diff` / `muse_approve` / `muse_complete_task`。
- `store.py`：タスクとセッションを JSON ファイルで保持（DB なし、GitHub Issue 経由なし）。セッション ID で前回の続きを `muse_get_session` から読める。
- `policy.py`：自動化の範囲。読み取りとテスト実行は自動、パッチ・コミット・Push・削除・デプロイは `needs_approval`＋ Muse レビュー必須、破壊・デプロイ系は拒否。Muse 本体の承認カードは別系統で、ここから自動承認はできない（権限ツールは読み取り専用）。
- `cli.py`：`serve` / `create-token` / `create-approval-key` / `status` / `complete`。既定は loopback（127.0.0.1:8765）。
- `oauth.py`：OAuth 2.1 認可サーバー（上記）。承認キーは 0600 ファイルまたは環境変数でのみ保持し、トークンは SHA-256 ハッシュでのみ状態ファイルに保存する。認可コードは使い捨て・10分、アクセストークンは1時間、リフレッシュトークンは30日でローテーションする。スコープは `mcp:tools` のみ。
- プラグイン対応：`tools/list` の各ツールに `annotations.readOnlyHint` を付与（読み取り系は true、送信・テスト実行・承認・完了は false）。`initialize` は 512 文字以内の自己完結した instructions を返す。

## 実行モデル（重要）

サーバー自身は任意の開発作業を実行しない。`muse_send_instruction` は、自動分類（テスト・git 読み取り）なら即実行し、それ以外は `awaiting_muse`（承認が必要なものは `needs_approval`）としてキューに残す。Muse が作業して `muse_complete_task`（または CLI `complete`）で結果・差分・テスト結果を書き込むと、ChatGPT 側は同じ MCP ツールで読める。外部からの指示が自動でコードを変更・Push することはない。

## 起動と接続手順（ユーザー操作が必要な部分）

1. この VM でトークンを作る（トークンはリポジトリ外、0600 で保存。コミットしない）：
   ```sh
   python3 -m muse_bridge.cli create-token --state /path/to/muse_bridge_state
   ```
2. サーバーを起動する（既定は loopback）：
   ```sh
   MUSE_BRIDGE_TOKEN=<token> python3 -m muse_bridge.cli serve --state /path/to/muse_bridge_state
   ```
   MCP URL（ローカル）：`http://127.0.0.1:8765/mcp`、ヘルス：`http://127.0.0.1:8765/health`
3. **公開 URL は発行していない。** ChatGPT（Web）から接続するには、次のいずれかをユーザーが別途用意する必要がある。公開前に、HTTPS・認証・中継サーバーの安全性の確認が必須であり、本件ではその確認ができていないため公開は行わない。
   - **OAuth 経路**：承認キーを作り、公開 URL 付きで起動する。承認キーはリポジトリ外、0600 で保存し、コミットしないこと。
     ```sh
     python3 -m muse_bridge.cli create-approval-key --state /path/to/muse_bridge_state
     MUSE_BRIDGE_OAUTH_APPROVAL_KEY=<approval-key> python3 -m muse_bridge.cli serve \
       --state /path/to/muse_bridge_state --public-url https://<公開ホスト>
     ```
     公開 URL の `/mcp` が MCP エンドポイント、メタデータは `/.well-known/oauth-protected-resource/mcp` と `/.well-known/oauth-authorization-server`。ChatGPT 側で認可画面が開いたら、この承認キーを入力して許可する（承認キーを ChatGPT やチャットに貼らないこと）。
   - **Secure MCP Tunnel 経路**（公開 URL を持たない場合）：ユーザーが Platform のトンネル設定でトンネルを作成し（トンネル ID と実行用 API キーは Platform 側で発行する。API キーをチャットに貼らないこと）、この VM で `tunnel-client` を起動して loopback の MCP サーバー（`http://127.0.0.1:8765/mcp`）へ転送する。ChatGPT 側では Plugins のカスタム MCP 追加で Connection に Tunnel を選び、トンネルを指定してプラグインとして作成する。
4. ChatGPT（Web）での接続：Plugins からカスタム MCP サーバーを追加し、名前と説明を付けて「プラグインとして作成」し、インストールする。プランと権限によっては追加できない場合がある（上記参照）。
5. **iPhone での使い方（追加操作は Web 側のみ）**：iPhone の ChatGPT アプリで同じアカウントのチャットを開き、インストール済みのプラグインを呼び出す（タスクを説明するか、プラグイン名を `@` で指定）。`muse_send_instruction` で指示を送り、`muse_get_status` / `muse_get_result` で結果を読む。セッション継続は同じ `session_id` を送ること。書き込みツールは確認が求められる。
6. 使い方（共通）：ChatGPT から `muse_send_instruction` で指示を送り、`muse_get_status` / `muse_get_result` で結果を読む。セッション継続は同じ `session_id` を送ること。

## 検証済み（ローカル）

- `tests/test_muse_bridge.py`：8 passed（従来の6件に加え、ツール注釈と initialize instructions、OAuth 全フロー：メタデータ、動的登録、承認キー検証、PKCE 交換、401 チャレンジ、リフレッシュのローテーション、失効）。
- E2E シミュレーション（ChatGPT 役の MCP クライアントで送信→承認→Muse 完了→MCP で結果取得、セッション維持）：成功。タスク ID とセッション ID はローカル検証用の一時値であり、公開用ではない。
- CLI スモーク：`create-approval-key`（0600 保存）、`--public-url` 付き起動で `/health` の `oauth: true`、認可サーバーメタデータ、未認証 `/mcp` の 401 を確認。スモーク用の承認キーは検証後に破棄済み。

## 未完了・未確認

- ChatGPT 側（Web / iPhone）からの実際の接続とテスト指示の送受信：未確認。公開 URL またはトンネルの用意と ChatGPT 側の操作はユーザーにしかできないため。
- iPhone アプリでの実機ツール呼び出し：未実証。構成としては Web で作成したプラグインを iPhone アプリで使えるが、実機で `muse_send_instruction` を呼んで結果を取得するまでの実証は、ユーザーの iPhone での操作待ち。その実証が得られるまで、接続完了とは判定しない。
- apex-6-private のローカルチェックアウト：プライベートリポジトリのためこの VM にはなく、ツールは「チェックアウトなし」エラーを返す。`repos.yaml` でチェックアウトを指定すれば使える。
- Hermes PR #11 / #16 は既知（Obsidian セッション／メモリ）。hermes-agent の PR #18 は存在しない（404 確認済み。公開 PR は #1、#8、#9、#10、#11）。重複を避けるため、本ブリッジは既存の Hermes / APEX-6 / Drive / Obsidian / Colab 連携には手を入れていない。
- 本番デプロイ、PR マージ、実データ削除、シークレット公開は行っていない。
