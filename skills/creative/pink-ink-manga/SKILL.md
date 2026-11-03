---
name: pink-ink-manga
description: "Render-ready visual art direction from three supplied manga references: uneven dark ink, coral/pink flat colors, lively facial linework, paper grain, restrained shading. Use to formulate prompts and evaluate existing images without requiring a local model."
version: 0.1.0
license: MIT
metadata:
  hermes:
    category: creative
    tags: [illustration, manga, anime, style-reference, lineart, pastel, image-art-direction]
    related_skills: [comfyui, image_gen]
---

# Pink Ink Manga — 画風再現スキル

## Use when
- ユーザーが「この3枚と同じ絵柄」「AIっぽいツヤを消す」「同じ線・彩色・表情にして」と依頼する場合。
- 画像を生成する前の画風指定、または生成結果のスタイル監査に使う。
- **これは画風記述スキル**。GPU・ローカルモデル・追加APIは不要。ただし実際に画像を描画するには別の画像生成ツールが必要。

## 固定の造形文法（重要度順）
1. **線**：黒に近いダークブラウンのインク輪郭。外周は太め、髪の内側と顔パーツは細め。線幅を均一にしない。端の短い途切れやスピード線は限定的に使用。
2. **色面**：ペールピンクの髪、コーラル色のアクセント、薄いピーチ系の肌、白〜オフホワイトの大きな余白。フラット塗りを主とする。
3. **顔**：大きめの目、切れ長の目尻、短い手描きまつげ、小さな口、細かい涙目・頬のハッチングなど、感情に合わせた線の密度。
4. **影**：影の段数は0〜1段。影を加える場合は狭い範囲の単色面にする。ぼかし・立体感・厚塗りは避ける。
5. **質感**：控えめな紙粒子と微細なインクの不規則さ。粒子を強くしすぎない。白い小さなハイライトは局所的に。
6. **背景**：基本は薄い無地、必要なら単色の暗い暖色。装飾は少なく、キャラクターの線を優先。
7. **構図**：縦長の単一キャラクター中心、見切れを大胆に使い、アイラインとシルエットを第一に配置。

## 出力手順
1. 内容（人物、服、ポーズ、構図）と画風（上記7項目）を別々に記述する。
2. ツール `pink_ink_style_prompt` があれば呼び、結果の `positive_prompt` と `negative_prompt` を画像生成ツールに渡す。
3. 画風参照画像を渡せる場合は、ユーザーが所有し適切な利用権を持つものをその場で参照する。元画像自体は公開GitHubに登録しない。
4. 結果は `STYLE_CHECKLIST.md` の各項目で評価する。線の強弱と影の少なさを最優先で直す。

## 絶対に混ぜない要素
- glossy 3D skin, cinematic bloom, volumetric lighting, soft airbrush gradients, realistic pores, highly rendered fabric, plastic sheen, over-smooth vector edges.
- 線の強弱を消す強いスムージング。全面を濃いトーンで覆う処理。

## 三つの色調プリセット
- `paper_coral`：白い紙とコーラルの対比（デフォルト）。
- `peach_line`：ピーチ系の肌／薄い背景を強調。
- `dark_rose`：濃いローズブラウン背景にピンク色の線画主体。

## 精度の限界
文章の画風指定のみでは元絵との一致は保証されない。高精度の再現には、画像生成側でのスタイル参照・img2img・ControlNetなどの対応機能が必要。スキル自身は生成器でも学習済みLoRAでもない。
