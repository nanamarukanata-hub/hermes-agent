# Hermes Deep Memory — 8プロジェクト調査からの統合

## 調査したプロジェクトとライセンス（2026-10-25、clone --depth 1で確認）

| プロジェクト | ライセンス | 取り入れた設計（コードはコピーしていない） |
|---|---|---|
| senjinthedragon/Smart-Memory | AGPL-3.0 | 多層記憶（長期/セッション/短期）、関係行列（感情記述子は最大6件）、トリガーによる活性化、シーン要約、Canon |
| leinstay/neptunia-bot | MIT | Discordでのメンション/名前トリガー、ランダム割り込み、沈黙チャンネルでの話題開始、態度（attitude）追跡、エピソードの重み付き保持 |
| yantrikos/chronicler | MIT | 3層書き込み契約（reflex/heuristic/canon）、per-memory `visible_to` ACLの機械的フィルタ、昇格/降格/忘却/retcon、反コンファブレーション |
| XaYS-101/Chaotic-lorebooks-Silly-Tavern | AGPL-3.0 | アーク（エピソード）への分割と封印、知識グラフ、思考バッファ、トークン予算内での注入、ドリフト監査 |
| pixelnull/sillytavern-DeepLore | MIT | Obsidian Vaultを正本とする2段階検索（キーワードで広く拾い、関連度で絞る）、関係グラフ、なぜ発火したかのトレース |
| Bredrumb/TomoriBot | AGPL-3.0 | Discordでの長期記憶・複数ペルソナ・RAG/STMの考え方（実装はHermesの単一Agent内で完結） |
| lunarblazepony/BlazeTracker | MIT | シーン状態（時刻・天候・場所・登場人物・衣装・小道具・緊張度）、関係の双方向追跡、章/イベントの時系列 |
| senjinthedragon/SillyTavern-Discord-Connector | AGPL-3.0（READMEはMIT+AGPLと表記） | Discordチャンネルへのルーティング、共有セッションの考え方 |

**ライセンス遵守の要点：** Hermes本体はMIT（NousResearch）であるため、AGPL-3.0のコードをそのまま取り込むことはできない。したがって本統合では、AGPLプロジェクトを含む全プロジェクトから**コードを1行もコピーしていない**。取り入れたのはアイデアとデータ設計のみであり、実装はすべて新規に書いたオリジナルコードである。MITプロジェクトについても同様にコードはコピーせず、設計のみを参考にし、上の表で出典を明示している。

## 実装（すべてObsidian Vault正本、単一Hermes Agent、別DBなし）

- `hermes_hybrid/deep_memory.py` — `DeepMemory`
  - 層：`reflex`（一時的シーン状態）/ `heuristic`（推論・要レビュー）/ `canon`（ durable・明示的昇格のみ）。
  - `visible_to` ACL：検索のランキング前に機械的にフィルタする。プロンプトの裁量に任せない。
  - 矛盾管理：否定表現による自動検出、明示的 supersede、解決（keep_new/keep_old/retcon）。削除はせず、旧記録は `superseded`/`retracted` として残る。
  - エピソード：アーク要約の封印。シーン：開始/更新/終了（終了時にエピソードへ封印）、`scene_context()` によるコンパクトな注入ブロック。
  - 関係グラフ：エンティティ登録、ペア関係（感情は最大6件・magnitude付き、knows/wants/status、履歴）。
  - トークン予算検索：`retrieve(query, budget_tokens, viewer, layers)`。層優先→関連度→新しさで並べ、予算を超える低優先項目は落とし、単独で予算を超える項目は切り詰めて入れる。トークン数は chars/4 の見積もりであることを明記。
  - ドリフト監査：`drift_audit()`（エンティティ重なり高・内容類似度低のペアを矛盾として記録）。
- `hermes_hybrid/vault_search.py` — `search_vault()`。Sessions/Memory/DeepMemory/HolographicMemory/CharacterState を横断し、BM25風スコア＋タグ/フロントマター絞り込み＋発火トレースを返す。2段階目のAI選別は、単一Agentの呼び出し側が `rerank` フックとして渡す形のみで、本モジュールはモデルを呼ばない。
- `hermes_hybrid/discord_proactive.py` — `ProactivePolicy` / `should_respond()` / `ProactiveWatcher`。送信は一切行わない純粋な判定ロジック。`dry_run=True` が既定。Discordアダプタ側の送信は既存のGatewayの責務のまま。
- `hermes_hybrid/gateway_integration.py` — `RPConversation.before_agent` にDeepMemory検索（予算500トークン）と直近シーンの注入を追加。DeepMemory記録が存在しない場合は無操作で、既存RP動作は変わらない。無効化は `HERMES_DEEP_MEMORY_ENABLED=0`。
- `hermes_hybrid/__init__.py` — 新モジュールの公開。

## 既存機能との重複整理

- 既存の `hermes_hybrid` メモリ検索（キーワード）は残し、DeepMemoryは層・ACL・矛盾という新しい軸を追加した。2つの検索を統合せず、RPフックでは両方を併記する（既存テストを壊さないため）。
- Holographicの事実ストア（信頼度・HRR）は残し、DeepMemoryはエピソード/関係/シーンという物語軸を担う。両者はVault内で別ツリーに住み、重複した事実を二重に持たない設計にはしていない（統合の第2段階で重複解消を検討すべき点として未完了に記載）。
- Discordの既存メンション応答は残し、Proactiveは判定ロジックのみを追加した。実際の送信ワイヤリングは行っていない（下記未完了）。

## 未完了事項

- DeepMemoryへのLLMによる自動抽出（会話からの事実・エピソード自動生成）は未実装。記録は呼び出し側の明示的な `add_memory` / `seal_episode` / `start_scene` による。自動抽出を入れると別モデル呼び出しが増え、単一Agentの制御から外れるため、設計判断として今回は見送った。
- Discord自発発言の実際の送信ワイヤリング（GatewayのDiscordアダプタへの組み込み、タイマーによる沈黙チャンネル監視）は未実装。判定ロジックと日次上限トラッカーは実装済みで、既定は dry-run。
- 実Discordでの送受信テストは未実施（トークン・実サーバーなし）。統合テストは判定ロジックのシミュレーションのみ。
- Holographic事実とDeepMemory記録の重複解消（同一事実の二重保持）は未実施。
