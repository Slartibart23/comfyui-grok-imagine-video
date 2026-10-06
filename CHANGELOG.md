# Changelog

## 3.0.0 — 2026-09-25
- **No more files in the ComfyUI output folder.** Videos download to `%TEMP%\grok_imagine`; *Grok Video Save* MOVES them to a folder and filename of your choice.
- **Grok Video Save 🎬** (replaces *Grok Save Video To Path*): `folder`, `filename` (+ connectable `filename_input`), numbering `name_000000` continuing from the highest existing number, never overwrites; optional `name.txt` (prompt), `name.json` (workflow), `name_base.jpg` (start image); preview with sound; buttons Reveal in Explorer / Open video / Save Last Frame / Delete this set.
- **Grok Image Save 🖼**: same behaviour for image batches (PNG with embedded workflow or JPG), preview + buttons.
- Video nodes show the finished clip with sound directly in the node (`show_preview`); every API node shows a status line with the billed cost.
- Registry-ready: `pyproject.toml` (PublisherId `slartibart23b`), publish workflow, `.comfyignore`, two example workflows.
- Legacy class ID `GrokSaveVideo` still loads (maps to Grok Video Save) - re-add the node once to get the new widgets.

## 2.1.1 — 2026-09-06
- `custom_model` values that are not a plausible model ID (e.g. a bare number left over from a node instance saved with an older version) are ignored with a console hint instead of being sent to the API.

## 2.1.0 — 2026-09-06
- **Edit node:** new `resolution` widget (`source` / `1k` / `2k`) — was missing in 2.0.0.
- **Cost transparency:** estimated cost from xAI list prices printed before every image request (inputs x $0.01 + outputs by tier); real billed cost from `cost_in_usd_ticks` on `info`.
- **`info`** now reports the true pixel size of every returned image, requested resolution/quality, number of source images, moderation flag.
- **Explicit `quality` defaults:** `low` for Generate, `medium` for Edit (what `auto` would silently pick) so the billed tier is visible; `auto`/`default` still available.
- **Edit uploads:** `upload_format` (png lossless / jpg) and `upload_max_side` (512-4096) for source images.
- New `fail_on_moderation` on both image nodes.
- Console warning when a retired model (`grok-imagine-image-quality`, `-pro`) is selected.
- Tooltips on every **output** socket (`OUTPUT_TOOLTIPS`) on all nodes; input tooltips rewritten (resolution = size, quality = detail tier, prices, how to reference source images in the prompt).

## 2.0.0 — 2026-09-05
- **Image:** default model `grok-imagine-image-2.0` with new `quality` widget (default/auto/low/medium); aspect ratios `21:9` and `5:2`; edit node accepts up to **5** source images (was 3). `grok-imagine-image-quality` stays selectable but is retired by xAI on 2026-11-02.
- **Video node:** every optional API input — `duration` 1-15 s, `resolution` 480p/720p/1080p, `generate_audio`, `image` (image-to-video), `reference_images` batch of up to 7 (reference-to-video), `voice_ids` (up to 3 preset voices), `image_max_side`. New outputs: `audio` (AUDIO, decoded soundtrack), `video` (native ComfyUI VIDEO object), `prompt` (text passthrough), `info` now includes the billed cost. Default resolution 720p, default model grok-imagine-video-1.5.
- **New nodes:** *Grok Imagine Video Edit* (`/v1/videos/edits`) and *Grok Imagine Video Extend* (`/v1/videos/extensions`); local MP4s are uploaded inline as base64.
- **Save node:** optional `prompt_text` input → `<name>_prompt.txt`, and `save_workflow_json` → `<name>_workflow.json`.
- Failed video jobs now surface xAI's `error.code` / `error.message`.
- English tooltips on every widget.

## 1.0.0 — 2026-08-04
- Initial release: Generate, Edit (1-3 images), Video (T2V/I2V), Save Video To Path.
