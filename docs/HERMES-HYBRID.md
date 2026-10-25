# Hermes Hybrid — Unified RP Engine + Obsidian-only storage

Single Hermes Agent構成を維持し、Sub-agentは導入しない。
Obsidian Vaultをすべての永続データの唯一の正本とし、SQLiteは
実行時の永続保存に使用しない。Gitは同期・バックアップ手段。

## アーキテクチャ

Discord
  -> Official Hermes Agent gateway (DM / channel / thread, per-user sessions)
  -> Unified RP Engine (`hermes_hybrid/rp_engine.py`)
       Character Consistency / Psychological State / Emotional Continuity /
       Character Cards (SillyTavern V1/V2/V3 + PNG) / World Book Retrieval /
       Scene Management
  -> Obsidian Storage (`hermes_hybrid/obsidian_store.py`)
       Conversations / Long-term Memory / Sessions / Character State /
       User Relationships
  -> Vault Git Sync / Recovery (`hermes_hybrid/vault_sync.py`)
  -> Linux MCP / Google Drive / Google Colab (`hermes_hybrid/mcp_bridge.py`,
     registry only — 既存連携を再実装しない)

## 移植元の切り分け（重複実装なし）

- 公式 NousResearch/hermes-agent (MIT): ゲートウェイ、Discord、MCP、
  プロバイダ切替、コンテキスト圧縮は公式のものを使う。本パッケージは
  再実装しない。
- Lzh-xbccz/hermes-roleplay-engine (MIT): カード項目、5次元心理プロファイル、
  ステータスバーによるシーン状態、World Bookキーワード索引、3スロット相当の
  セーブ概念をデータモデルとして新規実装した。生成プロンプト文面や
  キャラクター／世界観の具体設定は移植していない。
- OpenChara/OpenCharaAgent (Apache-2.0): SillyTavernカード互換、カードを
  唯一の身元情報源とする考え方、live/chatモード区分、送信者許可リストを
  採用した。サンドボックス、カードスタジオ、マーケット、映像パイプラインは
  移植していない。
- j1h00/hermes-agent (ライセンスファイルなし — コードをコピーしていない):
  fast-forwardのみのpull、非Git非空ディレクトリへのclone拒否、失敗時も
  既存チェックアウトを残す設計を参考に、push・競合マーカー・ bounded retry
  を加えた新規実装とした。デフォルトのリモートやトークン取得方法は
  持ち込んでいない。

## 既存開発ブランチとの関係

- nanamarukanata-hub/hermes-agent PR #11
  `feat/obsidian-session-backend-20261025` (Draft): すでにセッションを
  Vault JSON + Markdownで保持し、ランタイムSQLiteを廃止している。
  本パッケージはその設計（Vault正本、flock直列化、パス検証、起動拒否）
  と互換のレイアウトを採用し、RP・記憶・キャラクター状態・Vault同期を
  追加する層である。PR #11の移行ツール（read-only SQLite読み）は
  ランタイム依存ではない。
- nanamarukanata-hub/apex-6-private PR #16
  `feat/obsidian-only-storage-20261025`: 調査結果は別途記録する。
  APEX-6本体は有限CLIであり、本パッケージは常駐APEX-6サーバーを
  発明しない。

## 統合（2026-10-25 第2段階）

新規パッケージ追加ではなく、既存の実行経路へ接続した。

### 統一した保存形式・Vaultレイアウト・セッションキー

- セッション: `06_Automation/Hermes/Sessions/<session_id>/metadata.json`
  ＋ `Transcript.md`（PR #11 `SessionDB` と同一形式・同一パス。
  `hermes_hybrid` の `VaultStore` で PR #11 が書いたセッションを
  読み返せることを実コードで確認済み）。
- 記憶: `06_Automation/Hermes/Memory/<memory_id>.md`
  （PR #16 式の frontmatter＋本文。DBを使わない直接走査で検索）。
- キャラクター状態: `06_Automation/Hermes/CharacterState/<user_id>/<character_id>.json`
  （PR #11 に相当物がないため hybrid 層で新規定義。RP状態は
  (user, character) キーで、セッションIDには依存しないため、
  セッションリセット・再起動後も復元される）。
- セッションキー: ゲートウェイの `build_session_key` と同一規則
  （`hermes_hybrid.gateway_integration.discord_session_key`）。
  キー（コロン形式）は `GatewayRouting/sessions.json` で
  ゲートウェイの timestamp-hex セッションIDへ対応付けられる。
  キー自体を SessionDB のフォルダ名には使わない（コロンは
  PR #11 の ID 正規表現に含まれないため）。

### Discord Gatewayへの接続点

- `gateway/run.py` の `_handle_message_with_agent`:
  `_run_agent` 呼び出し直前に `RPConversation.before_agent` で
  カード・心理・感情・シーン・World Book一致・Vault記憶を
  `context_prompt` へ追加し、応答確定後に `after_agent` で
  RPターン状態を Vault へ保存する。カードが未設定の場合は
  空文字を返し、ゲートウェイ動作は変わらない。例外は
  ログのみでメッセージを落とさない。会話ターン自体は
  従来通り PR #11 SessionDB が保存する（重複書き込みなし）。
- `gateway/session.py` に `load_transcript_unified` を追加:
  PR #11 SessionDB のみを読む正本経路。既存の
  `load_transcript`（JSONLとの長い方優先・移行用）は
  互換のため変更していない。JSONLは移行用ミラーであり、
  正本は SessionDB である。
- 有効化: `HERMES_OBSIDIAN_VAULT` が必須。
  `HERMES_RP_ENABLED=0/false/no` で RPフックのみ無効化できる。
  カードはユーザーが Vault へ置いたものだけが使われ、
  本パッケージは具体的なキャラクター・世界観設定を作らない。
  RP独自のコンテンツフィルターは追加していない。

### SQLite監査（2026-10-25）

- セッション／ゲートウェイ／Discord の実行経路:
  `hermes_state.py`、`hermes_obsidian_state.py`、`gateway/session.py`、
  `gateway/run.py`、`gateway/platforms/discord.py`、`hermes_hybrid/` に
  sqlite import は 0 件。
- 残存するランタイム sqlite（セッション経路外のため本段階では
  統一していない）: `hermes_cli/kanban_db.py`・`backup.py`・`doctor.py`、
  `plugins/memory/retaindb`・`holographic`、`gateway/platforms/api_server.py`。
  これらを Vault 化すると Kanban・メモリプラグインの機能変更に
  なるため、別途設計判断が必要。テスト・移行ツール・テンプレートの
  sqlite はランタイム永続ではない。

## テスト

```sh
python3 -m pytest tests/test_hybrid.py tests/test_gateway_rp_integration.py -q -o addopts=""
```

テストは Discord トークン、ネットワーク、モデルを必要としない。
なお本ブランチの既存テスト `tests/gateway/test_session.py` 等は、
変更前から 8 failed / 15 errors（`HERMES_OBSIDIAN_VAULT` 未設定時の
SessionDB 起動拒否に起因する旧 SQLite 前提テスト）であり、
本統合による新規失敗は 0 件であることを変更前後で比較確認済み。

## 第3段階：残存SQLiteランタイム依存の撤廃とテスト正常化（2026-10-25）

### 移行済み（Vault JSON、sqlite import 0件）

- `plugins/memory/retaindb`：書き込み待ちキューを `06_Automation/Hermes/RetainQueue/<id>.json` へ移行。クラッシュ後のリプレイ、失敗時の `last_error` 記録、スレッド動作は維持。旧 `*.db` パスはディレクトリの stem として解釈する互換あり。
- `plugins/memory/holographic`（`store.py` / `retrieval.py` / `__init__.py`）：事実を `HolographicMemory/facts/<id>.json`、バンクを `banks/`、エンティティを `entities.json` へ移行。FTS5 はキーワード重なりスコアに置換（公開API・信頼度・HRRベクトルは維持、ベクトルは base64 保存）。`retrieval.py` の直接SQLはストアAPI経由に置換。
- `gateway/platforms/api_server.py`：`ResponseStore` を `ApiResponses/<response_id>.json`＋`conversations.json` へ移行。LRU上限、会話マップ、`__len__`、`delete`、`close` は維持。`":memory:"` は一時ディレクトリに置換。
- `hermes_cli/doctor.py`：`state.db` のセッション数チェックを Vault の `Sessions/` ディレクトリ数に置換。旧 `state.db` は開かず「旧アーティファクト」として報告のみ。WALチェックポイントは行わない（SQLite不使用）。
- `hermes_cli/backup.py`：`sqlite3.backup()` を廃止し、旧 `*.db` は不透明な旧アーティファクトとしてファイルコピーする。Vault時代にランタイムが `*.db` を書かないため、閉じた旧ファイルのコピーで十分であり、副作用として live DB の一貫性保証は失われる点を docstring に明記。
- 共通基盤：`hermes_hybrid/vault_kv.py`（原子書き込み＋flock、ID検証）。
- 移行ツール（読み取り専用、旧DBは削除・変更しない）：`scripts/migrate_sqlite_to_vault.py`（retaindb/holographic/api_server）、`scripts/migrate_kanban_to_vault.py`（kanban、ボード単位）。

### 未完了：Kanbanランタイム

`hermes_cli/kanban_db.py`（4,471行、トランザクション的クレーム/ハートビート/ラン管理、60件以上のテストが `sqlite3.Connection` API に依存）は、Vaultへの完全書き換えが本段階の範囲を超えるため、ランタイムはSQLiteのまま残っている。輸出スクリプトは用意済みだが、KanbanのVault化には呼び出し側（`kanban.py`、`kanban_diagnostics.py`、`kanban_specify.py`、ワーカー用ツール）全体のAPI変更が必要であり、別途設計判断が必要。

### テスト正常化

- 原因：`tests/gateway/test_session.py` の 8失敗・15エラーはすべて、`HERMES_OBSIDIAN_VAULT` 未設定で `SessionDB` が起動拒否することに起因していた。旧テストは `SessionDB(db_path=...)` で `.db` を指定していたが、Obsidian版は `db_path` を無視して環境変数のみを見る。
- 対応（無効化ではない）：テスト全体に per-test Vault を設定する autouse fixture を追加し、`SessionDB(db_path=...)` を `SessionDB()`（Vault経由）に変更。原子性テストは、存在しない旧 `_encode_content` フックではなく Vault の `_write` 失敗で置換し、意図（置換の原子性）を維持。さらに `append_to_transcript` / `rewrite_transcript` が GatewayRouting ディレクトリ未作成時に失敗する本番バグを修正（親ディレクトリを作成）。
- 結果：`tests/gateway/test_session.py`＋`tests/test_obsidian_session_state.py` は **94 passed, 8 subtests passed, 0 failed**。`tests/plugins/test_retaindb_plugin.py` は Vaultキュー仕様に合わせて更新し **67 passed**。`tests/gateway/test_api_server.py` は **138 passed**（pytest-asyncio等を備えたvenvで実行）。`tests/hermes_cli/test_backup.py` は **97 passed**（同venv）。
- 環境注記：システムpython（依存パッケージ不足）では `test_backup.py` の `TestRunPreUpdateBackup` 6件が `dotenv`/`yaml` 不足で失敗するが、変更前（stash）でも同一に失敗することを確認済みで、本変更によるものではない。
