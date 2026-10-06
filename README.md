# comfyui-grok-imagine-video

ComfyUI custom nodes for the **official xAI Grok Imagine API** — image generation and editing, video generation / editing / extension — with inline previews, cost display, and save nodes that put your files **exactly where you want them**. Nothing is ever written to the ComfyUI output folder. Every input and output has an English tooltip; hover over it in ComfyUI.

Uses the same `grok_api_key.txt` as [comfyui-grok-prompt-forge](https://github.com/Slartibart23/comfyui-grok-prompt-forge).

## Nodes (category **Grok → Imagine**)

| Node | What it does |
|---|---|
| **Grok Imagine Generate (xAI)** | Text-to-image. `grok-imagine-image-2.0`, `resolution` 1k/2k, `quality` low/medium, up to 10 images, 16 aspect ratios. Status line in the node shows model, pixel size and billed cost. |
| **Grok Imagine Edit 1-5 Images (xAI)** | Edit with up to five source images ("the woman from image 1 in the dress from image 2"). `resolution`, `quality`, lossless PNG upload. |
| **Grok Imagine Video (xAI)** | Text-to-video, image-to-video (`image`) or reference-to-video (`reference_images` batch up to 7 + `voice_ids`). 1-15 s, 480p/720p/1080p, audio on/off. **Shows the finished clip with sound in the node.** Outputs `frames`, `fps`, `audio`, `video` (native VIDEO), `video_path`, `prompt`, `info`. |
| **Grok Imagine Video Edit (xAI)** | Change an existing MP4 (<= 8.7 s) with a prompt. Same outputs and preview. |
| **Grok Imagine Video Extend (xAI)** | Continue an MP4 from its last frame by N seconds. Same outputs and preview. |
| **Grok Video Save 🎬** | Moves the finished MP4 (no re-encoding) to **your folder** with **your filename**, numbered `name_000000`, `name_000001`, ... Optional `name.txt` (prompt), `name.json` (workflow), `name_base.jpg` (start image). Preview with sound + buttons. |
| **Grok Image Save 🖼** | Same for images from Generate / Edit: your folder, your filename, numbered, optional `.txt` and `.json`, PNG (with embedded workflow) or JPG. Preview + buttons. |

## How files flow

```
[Grok Imagine Video] --video_path--> [Grok Video Save] --> D:\Videos\clip_000000.mp4
                     --prompt------>   prompt_text          D:\Videos\clip_000000.txt
[Load Image] ----------------------->   base_image           D:\Videos\clip_000000.json
                                                             D:\Videos\clip_000000_base.jpg
```

1. The video node downloads the MP4 from xAI into the **Windows temp folder** (`%TEMP%\grok_imagine`) and shows it in the node.
2. Grok Video Save **moves** it from there to your `folder` under your `filename` — no copy stays behind, no re-encoding, sound kept.
3. Companion files share the same number. The counter continues from the highest number already in the folder; **nothing is ever overwritten.**

`filename_input` (connect a text) overrides the `filename` field. Placeholders `%date%`, `%time%`, `%date:yyyy-MM-dd%` work in both.

## Buttons on the save nodes

| Button | Action |
|---|---|
| 📂 Reveal in Explorer | Opens the folder with the file selected |
| ▶ Open video / 🖼 Open image | Opens it in the Windows default player / viewer |
| 🖼 Save Last Frame | Writes `name_000000_last.jpg` (video only) |
| 🗑 Delete this set | Deletes the file and all its companions (with confirmation) |

The buttons only ever act on files this node saved in the current ComfyUI session.

## Image size vs. quality

| Widget | Meaning | Values | Price on image-2.0 |
|---|---|---|---|
| `resolution` | pixel size | `1k` (~1 MP) · `2k` (~4 MP) | 2k = +$0.02 per image |
| `quality` | detail / compute tier | `low` · `medium` · `auto` · `default` | 1k/low $0.04 · 2k/low $0.06 · 1k/medium $0.06 · 2k/medium $0.08 |

Edits bill an extra **$0.01 per source image**. The console prints the estimate before each request; the node status line shows what xAI actually billed.

## Installation

**ComfyUI Manager:** search for *Grok Imagine* and install.

**Manual (portable):**
```
cd ComfyUI\custom_nodes
git clone https://github.com/Slartibart23/comfyui-grok-imagine-video
..\..\python_embeded\python.exe -m pip install -r comfyui-grok-imagine-video\requirements.txt
```
Restart ComfyUI, then press **Ctrl+F5** in the browser once (loads the preview script). ffmpeg is needed for audio decoding, last-frame export and the status probe; `imageio-ffmpeg` (in requirements) brings its own.

**Upgrading from 2.x:** replace the files, restart, Ctrl+F5. Delete and re-add the video nodes and the save node once — new widgets were added and the old *Grok Save Video To Path* is replaced by *Grok Video Save*. Example workflows are in `example_workflows/`.

## API key

`api_key` widget → `XAI_API_KEY` environment variable → `grok_api_key.txt` in the ComfyUI root, the portable root or this folder. Never share workflows that contain your key.

## Model notes (September 2026)

- `grok-imagine-image-quality` is retired on 2026-11-02 (then served as 2.0 / low). The node warns if it is still selected.
- Video editing needs `grok-imagine-video` (1.0); everything else works best on `grok-imagine-video-1.5`.
- Reference-to-video is capped at 720p by the API.

## License

MIT
