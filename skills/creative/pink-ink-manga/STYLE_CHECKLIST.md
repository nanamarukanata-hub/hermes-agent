# Pink Ink Manga — スタイル監査（100点）

- 25点：可変線幅とダークブラウン〜黒の手描きインク輪郭が残っている。
- 20点：白／ピーチ／コーラル／ピンク中心の狭い色数。全面グラデーションに逃げない。
- 20点：顔の表情が目尻・まつげ・頬ハッチング・小さな口の線で表現されている。
- 15点：セル影は0〜1段、厚塗り感や過度の肌テカリがない。
- 10点：紙粒子は微細で、線を潰さない。
- 10点：構図に余白があり、キャラが背景装飾に埋もれていない。

## 失敗例と改善

- **AI的なテカリ** → diffuse light / volumetric light / glossy surface を除外し、flat color + minimal highlight を強調。
- **線が細すぎる** → thicker outer silhouette, expressive tapered ink line を追加。
- **塗りが立体的すぎる** → cel shading 0–1 levels, no soft gradient を追加。
- **くすんで見える** → warm peach paper, clean pale pink, coral accent を追加。
- **絵が機械的** → intentionally uneven hand-inked contours, short broken strokes を追加。
