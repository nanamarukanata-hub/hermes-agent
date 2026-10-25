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

## テスト

```sh
python3 -m pytest tests/ -q
```

テストは Discord トークン、ネットワーク、モデルを必要としない。
