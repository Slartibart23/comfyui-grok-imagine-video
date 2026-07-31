# comfyui-grok-imagine

ComfyUI custom nodes for the **official xAI Grok Imagine API** — image generation, multi-image editing, and video generation, using the same API key as any other xAI integration.

## Nodes

| Node | Purpose |
|---|---|
| **Grok Imagine Generate (xAI)** | Text-to-image via `/v1/images/generations`. Up to 10 images per request (`n`), aspect ratio incl. `auto`, resolution `1k`/`2k`. Outputs an IMAGE batch plus an `info` string (moderation flag, resolved model, and any extra metadata the API returns). |
| **Grok Imagine Edit 1-3 Images (xAI)** | Image editing via `/v1/images/edits` with **up to three source images** (`image_1` required, `image_2`/`image_3` optional). Image order matters; output ratio follows the first image unless overridden via `aspect_ratio`. |
| **Grok Imagine Video (xAI)** | Text-to-video (no image connected) or image-to-video (IMAGE connected) via `/v1/videos/generations`. Handles the async submit-and-poll flow, downloads the temporary MP4 immediately, and outputs decoded `frames` (IMAGE batch), `fps`, the local `video_path`, and `info`. |
| **Grok Save Video To Path** | Copies the generated video to **any folder on any drive** with a **custom filename**. Creates the folder if needed; without `overwrite`, existing names get `_001`, `_002`, ... appended. Outputs the final `saved_path`. |

## Model selection

The model dropdowns are defined as editable lists at the top of `grok_imagine_nodes.py` (`IMAGE_MODELS`, `VIDEO_MODELS`). When xAI ships new models, add the ID there and restart ComfyUI — or type any model ID into the `custom_model` field, which overrides the dropdown without touching code. Check available IDs in the [xAI Console](https://console.x.ai) or via `GET /v1/models`. Note: only `grok-imagine-image-quality`, `grok-imagine-video`, and `grok-imagine-video-1.5` are confirmed from official docs; verify the other image IDs against your console.

## Installation (Windows, Portable ComfyUI)

1. Clone or copy into `ComfyUI\custom_nodes\comfyui-grok-imagine\`
2. From the portable root: `python_embeded\python.exe -m pip install -r ComfyUI\custom_nodes\comfyui-grok-imagine\requirements.txt`
3. Restart ComfyUI — nodes appear under **Grok → Imagine**.

## API key

Same resolution order as the companion pack [comfyui-grok-text-refine](https://github.com/Slartibart23/comfyui-grok-text-refine): `api_key` widget → `XAI_API_KEY` env var → `grok_api_key.txt` (searched in the ComfyUI root, the portable root, and this pack's folder). One key file serves both packs.

## Notes & limits

- Every API node has a **`seed`** input acting as a ComfyUI cache-breaker: with the control set to *randomize* (default behavior), each queue run forces a fresh API call. The seed is **not** sent to the API — xAI documents no seed parameter; variation comes from the server. Set the control to *fixed* to deliberately reuse the cached result and avoid paying for an identical request.

- **1080p** video is only supported on `grok-imagine-video-1.5` in image-to-video mode; video pricing is per second and scales with duration and resolution.
- Image/video URLs returned by the API are **temporary** — the video node downloads immediately; use *Grok Save Video To Path* for permanent storage.
- The hosted Imagine API applies **content moderation**; the `info` output surfaces the `respect_moderation` flag so filtered results are visible.
- Video generation can take minutes; tune `poll_interval_seconds` / `poll_timeout_seconds` for long jobs.

## License

MIT
