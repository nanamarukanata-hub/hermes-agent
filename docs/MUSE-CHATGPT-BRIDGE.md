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

### ChatGPT のカスタム MCP 接続（2026-10-25 時点の公開情報まとめ）

- カスタム MCP サーバーは **Web 版のみ**で、モバイルアプリからは追加できない。iPhone の ChatGPT アプリから直接追加する経路は、現行の仕様では存在しない。
- フルな読み書き対応は Business / Enterprise / Edu（ベータ）に限られ、Pro は読み取り・取得のみとされる。Plus / Free の扱いは資料によって異なり、自分のアカウントで確認が必要。
- 認証は OAuth または認証なしとされ、Bearer ヘッダーを送る選択肢は ChatGPT の接続画面にないとされる資料がある。したがって本ブリッジの Bearer トークンは汎用 MCP クライアント（Claude / Codex / curl）向けであり、ChatGPT 向けの認証はトンネル側のアクセス制御（例：Cloudflare Access の許可メール）に委ねる設計にしている。
- **結論：iPhone アプリから直接、という最終目標の形は現行 ChatGPT では未達成。** iPhone 上の Safari で ChatGPT Web を使う形、または Codex を使う形が現実的な代替である。公開 HTTPS エンドポイントは本件では発行していない（下記参照）。

## 実装（`muse_bridge/`、依存なし・Python 標準ライブラリのみ）

- `server.py`：`POST /mcp`（JSON-RPC：initialize / tools/list / tools/call / ping）と `GET /health`。ツールは `muse_send_instruction` / `muse_get_status` / `muse_get_result` / `muse_list_sessions` / `muse_get_session` / `muse_run_tests` / `muse_get_diff` / `muse_approve` / `muse_complete_task`。
- `store.py`：タスクとセッションを JSON ファイルで保持（DB なし、GitHub Issue 経由なし）。セッション ID で前回の続きを `muse_get_session` から読める。
- `policy.py`：自動化の範囲。読み取りとテスト実行は自動、パッチ・コミット・Push・削除・デプロイは `needs_approval`＋ Muse レビュー必須、破壊・デプロイ系は拒否。Muse 本体の承認カードは別系統で、ここから自動承認はできない（権限ツールは読み取り専用）。
- `cli.py`：`serve` / `create-token` / `status` / `complete`。既定は loopback（127.0.0.1:8765）。

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
3. **公開 URL は発行していない。** ChatGPT（Web）から接続するには、HTTPS 終端＋認証付きリレー（例：Cloudflare Access 等）をユーザーが別途用意し、その公開 URL を ChatGPT の接続画面に入れる必要がある。公開前に、HTTPS・認証・中継サーバーの安全性の確認が必須であり、本件ではその確認ができていないため公開は行わない。
4. ChatGPT（Web）での接続：ChatGPT の Plugins / Apps & Connectors からカスタム MCP サーバーを追加し、公開 URL を指定する。プランと権限によっては追加できない場合がある（上記参照）。iPhone アプリからは追加できないため、iPhone では Safari で ChatGPT Web を開く形になる。
5. 使い方：ChatGPT から `muse_send_instruction` で指示を送り、`muse_get_status` / `muse_get_result` で結果を読む。セッション継続は同じ `session_id` を送ること。

## 検証済み（ローカル）

- `tests/test_muse_bridge.py`：6 passed（認証 401、tools/list、送信→承認→完了→結果取得、セッション継続、tmp チェックアウトでのテスト実行と差分取得、apex-6-private のチェックアウトなしエラー）。
- E2E シミュレーション（ChatGPT 役の MCP クライアントで送信→承認→Muse 完了→MCP で結果取得、セッション維持）：成功。タスク ID とセッション ID はローカル検証用の一時値であり、公開用ではない。

## 未完了・未確認

- ChatGPT 側（Web / iPhone）からの実際の接続とテスト指示の送受信：未確認。公開 HTTPS エンドポイントがなく、ChatGPT 側の操作はユーザーにしかできないため。
- iPhone アプリからの直接接続：現行 ChatGPT の仕様上、未対応（Web 経由が代替）。
- apex-6-private のローカルチェックアウト：プライベートリポジトリのためこの VM にはなく、ツールは「チェックアウトなし」エラーを返す。`repos.yaml` でチェックアウトを指定すれば使える。
- Hermes PR #11 / #16 は既知（Obsidian セッション／メモリ）。PR #18 は確認中（別途報告）。重複を避けるため、本ブリッジは既存の Hermes / APEX-6 / Drive / Obsidian / Colab 連携には手を入れていない。
- 本番デプロイ、PR マージ、実データ削除、シークレット公開は行っていない。
