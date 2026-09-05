# Changelog

## 2.0.0 — 2026-09-05
- **Image:** default model `grok-imagine-image-2.0` with new `quality` widget (default/auto/low/medium); aspect ratios `21:9` and `5:2`; edit node accepts up to **5** source images (was 3). `grok-imagine-image-quality` stays selectable but is retired by xAI on 2026-11-02.
- **Video node:** every optional API input — `duration` 1-15 s, `resolution` 480p/720p/1080p, `generate_audio`, `image` (image-to-video), `reference_images` batch of up to 7 (reference-to-video), `voice_ids` (up to 3 preset voices), `image_max_side`. New outputs: `audio` (AUDIO, decoded soundtrack), `video` (native ComfyUI VIDEO object), `prompt` (text passthrough), `info` now includes the billed cost. Default resolution 720p, default model grok-imagine-video-1.5.
- **New nodes:** *Grok Imagine Video Edit* (`/v1/videos/edits`) and *Grok Imagine Video Extend* (`/v1/videos/extensions`); local MP4s are uploaded inline as base64.
- **Save node:** optional `prompt_text` input → `<name>_prompt.txt`, and `save_workflow_json` → `<name>_workflow.json`.
- Failed video jobs now surface xAI's `error.code` / `error.message`.
- English tooltips on every widget.

## 1.0.0 — 2026-08-04
- Initial release: Generate, Edit (1-3 images), Video (T2V/I2V), Save Video To Path.
