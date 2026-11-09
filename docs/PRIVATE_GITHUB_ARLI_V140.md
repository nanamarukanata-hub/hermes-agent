# Private GitHub LoRA → Arli V140

**共有用Markdown仕様書**。LoRAの重みは非公開GitHub Releaseにのみ保存し、Hugging Faceへの公開・ミラーリングを行わず、Arli V140へ適用するための転送・検証方式をまとめる。

## Architecture

```text
Private GitHub Release (LoRA .safetensors)
    │ GitHub Contents: Read authorization
    ▼
Client / Muse VM (SHA-256 verification)
    │ authenticated binary transfer
    ▼
Arli SD.Next-compatible API
    ├─ PUT /sdapi/v1/upload (upstream SD.Next route)
    ├─ POST /sdapi/v1/txt2img (model: SDXL-waiNSFWIllustrious_v140)
    └─ generation metadata (LoRA hash verification)
```

- **LoRAの保管先**: private GitHub Releaseのみ。公開GitHubにモデル重みを置かない。
- **推論先**: Arli V140。別GPUや別画像生成サービスを使わない。
- **接続手段**: GitHubの認証付きRelease Asset取得と、SD.Nextで公開されているアップロードAPI。
- **セキュリティ**: GitHubの読み取り用トークンとArli APIキーを別々の環境変数で管理。公開文書・プロンプト・ログに秘密情報を記録しない。

## Publicly available implementations used

1. [SD.Next upload API](https://github.com/vladmandic/sdnext/blob/main/modules/api/upload.py): `PUT /sdapi/v1/upload` accepts binary data and the `filename` / `path` headers. The destination directory must already exist on the server.
2. [SD.Next LoRA loader](https://github.com/vladmandic/sdnext/blob/main/modules/lora/lora_load.py): can load a model weight file from a path visible to the inference process.
3. [SD.Next LoRA generation metadata](https://github.com/vladmandic/sdnext/blob/main/modules/lora/extra_networks_lora.py): LoRA hashes can be written to generation metadata.
4. [Arli API](https://arliai.com/quick-start): SD.Next-compatible image generation interface.

**Compatibility boundary:** SD.Next upstream supports file upload, but Arli's hosted service must accept that endpoint and expose the uploaded LoRA to its inference workers. Compatibility with `txt2img` alone does not establish write access or shared storage. The implementation checks API responses and LoRA hashes instead of assuming success.

## Execution

Code paths in the private implementation repository:

- `scripts/private_lora_bridge.py` — authenticated private GitHub Release fetch and SHA-256 verification.
- `scripts/private_lora_arli.py` — Arli capability probe, upload, generation comparison and proof reporting.
- `tests/test_private_lora_arli.py` — offline verification.

Credentials (configure securely in the runtime; never commit the values):

```text
GITHUB_TOKEN         — fine-grained PAT for the private repository, Contents: Read
ARLIAI_API_KEY       — Arli account API key
```

Capability check:

```sh
python3 scripts/private_lora_arli.py probe
```

Transfer the private LoRA and request actual inference tests:

```sh
python3 scripts/private_lora_arli.py apply \
  --repo OWNER/PRIVATE_REPOSITORY \
  --tag YOUR_RELEASE_TAG \
  --asset YOUR_LORA.safetensors \
  --sha256 YOUR_EXACT_64_HEX_SHA256 \
  --output arli_private_lora_results \
  --yes-upload
```

This command explicitly sends the model bytes to **Arli-operated servers**. The GitHub repository and Release remain private, but Arli will receive the LoRA for inference. The application evaluates the baseline and LoRA-tagged image with the same seed and stores `report.json`.

## Verification criteria

- Authenticated GitHub download succeeds and the exact SHA-256 matches.
- The Arli upload endpoint confirms the correct number of bytes.
- The generation response explicitly identifies the expected LoRA hash in metadata.
- A changed image alone, a successful file download, or a successful upload alone is **not proof** that Arli applied the LoRA.

If the hosted Arli API rejects upload or the generation worker cannot see the model, the script stops with an error or reports that loading was not evidenced. A public SD.Next capability cannot override Arli's server-side restrictions.

## Privacy

This public Markdown page includes **no LoRA weights, private Release URLs, access tokens, or private repository identifiers**. Share this document freely without changing the GitHub repository's private status.
