# comfyui-grok-imagine-video

ComfyUI custom nodes for the **official xAI Grok Imagine API** — image generation, multi-image editing, video generation / editing / extension — using the same API key as the companion pack [comfyui-grok-prompt-forge](https://github.com/Slartibart23/comfyui-grok-prompt-forge). Every widget has an English tooltip; hover over it in ComfyUI.

## Nodes (category **Grok → Imagine**)

| Node | Purpose |
|---|---|
| **Grok Imagine Generate (xAI)** | Text-to-image via `/v1/images/generations`. `grok-imagine-image-2.0` (default) with `quality` auto/low/medium, up to 10 images per request, 16 aspect ratios incl. `auto`, `21:9`, `5:2`, resolution 1k/2k. Outputs `images`, `prompt`, `info` (model, moderation flag, billed cost). |
| **Grok Imagine Edit 1-5 Images (xAI)** | Image editing via `/v1/images/edits` with up to **five** source images (`image_1` required). Order matters; the output keeps the first image's ratio unless overridden. |
| **Grok Imagine Video (xAI)** | One node for all three generation modes: **text-to-video** (nothing connected), **image-to-video** (`image` connected = first frame), **reference-to-video** (`reference_images` batch of up to 7 + optional `voice_ids`). Duration 1-15 s, 480p/720p/1080p, `generate_audio`. Submits, polls, downloads the MP4 and outputs `frames`, `fps`, `audio`, `video` (native VIDEO), `video_path`, `prompt`, `info`. |
| **Grok Imagine Video Edit (xAI)** | Modify an existing MP4 (≤ 8.7 s) with a prompt via `/v1/videos/edits`. Same outputs. |
| **Grok Imagine Video Extend (xAI)** | Continue an MP4 from its last frame by `duration` seconds via `/v1/videos/extensions`. Same outputs. |
| **Grok Save Video To Path** | Copies the MP4 (no re-encode) to **any folder on any drive** with a custom filename, optionally writing `<name>_prompt.txt` and `<name>_workflow.json` next to it. |

## Typical wiring

```
Text-to-video, saved with prompt + workflow:
[Grok Imagine Video] --video_path--> [Grok Save Video To Path]
                     --prompt------> (prompt_text)

Use the native ComfyUI video nodes:
[Grok Imagine Video] --video--> [Save Video] / [Preview Video]

Save with Video Save Plus (Custom Path) without re-encoding:
[Grok Imagine Video] --video_path--> [Video Save Plus].source_video_path
                     --prompt------> [Video Save Plus].prompt_text
                     --video-------> [Video Save Plus].video   (alternative: re-encode with CRF etc.)

Extend a clip you just generated:
[Grok Imagine Video] --video_path--> [Grok Imagine Video Extend] --video_path--> [Grok Save Video To Path]
```

## Video node in detail

| Widget | What it does |
|---|---|
| `model` | `grok-imagine-video-1.5` (default): T2V, I2V, reference-to-video, 1080p, 15 s, audio, preset voices. `grok-imagine-video` (1.0): older, required for **editing**. |
| `duration` | 1-15 seconds. Billed per second, more at higher resolution. |
| `aspect_ratio` | `default` = 16:9 for text-to-video, the input image's ratio for image-to-video. |
| `resolution` | `480p` (API default), `720p` (node default), `1080p` (1.5 only, T2V + I2V; reference-to-video is capped at 720p). |
| `image` | Connect for image-to-video. Cannot be combined with `reference_images`. |
| `reference_images` | An IMAGE **batch** (use a Batch Images node) of up to 7 pictures whose people/objects/clothes should appear. Refer to them as `<IMAGE_1>`, `<IMAGE_2>`… in the prompt. |
| `voice_ids` | Up to 3 preset voices (`eve, leo`), referenced as `<AUDIO_0>`… in the prompt. Same roster as xAI Text-to-Speech. |
| `generate_audio` | Off = silent video. |
| `extract_frames` / `extract_audio` | Decode the MP4 into `frames` (IMAGE) and `audio` (AUDIO). Turn off to save time when you only need the file. |
| `poll_*` | Long 1080p jobs can take minutes; raise `poll_timeout_seconds` if needed. |

`info` contains the model the API used, duration, `respect_moderation` and the exact billed cost (`cost_in_usd_ticks` → USD).

## Model notes (September 2026)

- `grok-imagine-image-quality` is **retired on 2026-11-02**; requests are then served by `grok-imagine-image-2.0` at `quality=low`. The node already defaults to 2.0.
- On `grok-imagine-image-2.0`, omitting `quality` means `auto` (= low for generation, medium for editing). Images are billed at the quality they are served at.
- Text-to-video on `grok-imagine-video-1.5` internally runs text-to-image then image-to-video; you still send one request.
- Model dropdowns are plain lists at the top of `grok_imagine_nodes.py`; `custom_model` overrides them without code changes. Check `GET /v1/models` or the xAI console for new IDs.

## Installation (Windows, Portable ComfyUI)

1. Copy this folder to `ComfyUI\custom_nodes\comfyui-grok-imagine-video\`
2. From the portable root: `python_embeded\python.exe -m pip install -r ComfyUI\custom_nodes\comfyui-grok-imagine-video\requirements.txt` (imageio + imageio-ffmpeg bring their own ffmpeg for frame/audio decoding)
3. Restart ComfyUI.

**Upgrading from 1.x:** replace the files. Node class IDs are unchanged; existing workflows load. Re-add the Video node once to see the new sockets on old instances.

> The Video node's outputs are now `frames, fps, audio, video, video_path, prompt, info`. In workflows saved with 1.x, links that were attached to `video_path` or `info` land on the new `audio`/`video` sockets — reconnect those two links once.

## API key

`api_key` widget → `XAI_API_KEY` env var → `grok_api_key.txt` (searched in the ComfyUI root, the portable root, and this pack's folder). One key file serves both packs. Never share workflows containing your key.

## Notes & limits

- API video/image URLs are temporary — the nodes download immediately; use *Save Video To Path* (or the native Save Video node via the `video` output) for permanent storage.
- Video editing input: `.mp4`, H.264/H.265/AV1, max 8.7 s; output ≤ 720p, same duration/ratio.
- Video extension output resolution matches the input, capped at 720p; `duration` is the added length.
- The hosted API applies content moderation; `info` surfaces `respect_moderation`.

## License

MIT
