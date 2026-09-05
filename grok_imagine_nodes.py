"""
Grok Imagine — ComfyUI custom nodes
Image generation, multi-image editing, video generation / editing / extension
via the official xAI Grok Imagine API.

v2.0.0 (September 2026)
- Image: grok-imagine-image-2.0 default with `quality` (auto/low/medium),
  aspect ratios 21:9 and 5:2, edit node accepts up to 5 source images.
  (grok-imagine-image-quality is retired on 2026-11-02.)
- Video: every optional API input — duration up to 15 s, 480p/720p/1080p,
  generate_audio, image-to-video, reference-to-video (up to 7 reference
  images + up to 3 preset voices), plus new outputs: AUDIO (decoded track),
  VIDEO (native ComfyUI video object), the prompt as text, and info with the
  billed cost. Two new nodes: Video Edit and Video Extend.
- Save node can additionally write <name>_prompt.txt and <name>_workflow.json.
- English tooltips on every widget.

Node class IDs from 1.x are unchanged.
License: MIT
"""

import base64
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import wave

import requests

# ---------------------------------------------------------------------------
# MODEL LISTS — edit when xAI ships new models, then restart ComfyUI.
# `custom_model` on each node overrides the dropdown when non-empty.
# Verified against docs.x.ai on 2026-09-05.
# ---------------------------------------------------------------------------
IMAGE_MODELS = [
    "grok-imagine-image-2.0",      # current; quality auto/low/medium; 14 ratios
    "grok-imagine-image",          # 1.0, still served
    "grok-imagine-image-quality",  # RETIRED 2026-11-02 -> redirects to 2.0/low
]
VIDEO_MODELS = [
    "grok-imagine-video-1.5",      # T2V/I2V/reference, 1080p, 15 s, audio, voices
    "grok-imagine-video",          # 1.0; required for video EDITING
]

IMAGE_ASPECT_RATIOS = ["auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3",
                       "2:1", "1:2", "21:9", "5:2", "19.5:9", "9:19.5",
                       "20:9", "9:20"]
VIDEO_ASPECT_RATIOS = ["default", "16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"]
VIDEO_RESOLUTIONS = ["default", "480p", "720p", "1080p"]
IMAGE_QUALITY = ["default", "auto", "low", "medium"]

DEFAULT_BASE_URL = "https://api.x.ai/v1"
DEFAULT_KEY_FILE = "grok_api_key.txt"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _find_comfy_root():
    try:
        import folder_paths
        return folder_paths.base_path
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.abspath(os.path.join(here, "..", ".."))
    return candidate if os.path.isdir(candidate) else os.getcwd()


def _output_dir():
    try:
        import folder_paths
        base = folder_paths.get_output_directory()
    except Exception:
        base = os.path.join(_find_comfy_root(), "output")
    d = os.path.join(base, "grok_imagine")
    os.makedirs(d, exist_ok=True)
    return d


def _resolve_api_key(api_key_widget, api_key_file):
    if api_key_widget and api_key_widget.strip():
        return api_key_widget.strip()
    env_key = os.environ.get("XAI_API_KEY", "").strip()
    if env_key:
        return env_key
    if api_key_file and api_key_file.strip():
        p = api_key_file.strip()
        root = _find_comfy_root()
        candidates = [p] if os.path.isabs(p) else [
            os.path.join(root, p),
            os.path.join(os.path.dirname(root), p),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), p),
        ]
        for path in candidates:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    k = f.read().strip()
                if k:
                    return k
        print(f"[Grok Imagine] Key file not found. Searched: {', '.join(candidates)}")
    raise RuntimeError(
        "[Grok Imagine] No xAI API key found. Provide it via the api_key widget, "
        "the XAI_API_KEY environment variable, or grok_api_key.txt in the "
        "ComfyUI root. Get a key at https://console.x.ai"
    )


def _pick_model(dropdown, custom_model):
    return custom_model.strip() if custom_model and custom_model.strip() else dropdown


def _to_numpy(image):
    import numpy as np
    arr = image
    if hasattr(arr, "cpu"):
        arr = arr.cpu().numpy()
    return np.asarray(arr)


def _tensor_to_data_uri(image, image_index=0, max_side=2048, fmt="jpeg"):
    import numpy as np
    from PIL import Image

    arr = _to_numpy(image)
    if arr.ndim == 4:
        arr = arr[min(int(image_index), arr.shape[0] - 1)]
    arr = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    if max_side and max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    if fmt == "png":
        img.save(buf, format="PNG")
        mime = "image/png"
    else:
        img.convert("RGB").save(buf, format="JPEG", quality=95)
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"


def _batch_to_data_uris(image, max_items, max_side=2048, fmt="png"):
    """Every image of a batch (B,H,W,C) becomes its own data URI."""
    arr = _to_numpy(image)
    n = arr.shape[0] if arr.ndim == 4 else 1
    n = min(n, max_items)
    return [_tensor_to_data_uri(arr, i, max_side=max_side, fmt=fmt) for i in range(n)]


def _b64_or_url_to_array(item, timeout=120):
    import numpy as np
    from PIL import Image

    raw = None
    if item.get("b64_json"):
        raw = base64.b64decode(item["b64_json"])
    elif item.get("url"):
        r = requests.get(item["url"], timeout=timeout)
        r.raise_for_status()
        raw = r.content
    if raw is None:
        raise RuntimeError(f"[Grok Imagine] Image result has neither b64 nor url: {item}")
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    return np.asarray(img).astype("float32") / 255.0


def _stack_images(arrays):
    import numpy as np

    h, w = arrays[0].shape[:2]
    fixed = []
    for a in arrays:
        if a.shape[:2] != (h, w):
            from PIL import Image
            img = Image.fromarray((a * 255).astype("uint8")).resize((w, h), Image.LANCZOS)
            a = np.asarray(img).astype("float32") / 255.0
        fixed.append(a)
    batch = np.stack(fixed, axis=0)
    try:
        import torch
        return torch.from_numpy(batch)
    except ImportError:
        return batch


def _post_json(url, key, payload, timeout, tag, retries=2):
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = RuntimeError(f"[{tag}] HTTP {resp.status_code}: {resp.text[:300]}")
                print(f"[{tag}] HTTP {resp.status_code}; retrying...")
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"[{tag}] HTTP {resp.status_code}: {resp.text[:600]}")
            return resp.json()
        except (requests.Timeout, requests.ConnectionError) as e:
            last_error = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"[{tag}] Request failed after retries: {last_error}")


def _cost_str(obj):
    usage = obj.get("usage") if isinstance(obj, dict) else None
    ticks = None
    if isinstance(usage, dict):
        ticks = usage.get("cost_in_usd_ticks")
    if ticks is None and isinstance(obj, dict):
        ticks = obj.get("cost_in_usd_ticks")
    if ticks is None:
        return ""
    try:
        return f"cost_usd={int(ticks) / 1e10:.6f}"  # 1 USD = 1e10 ticks
    except Exception:
        return f"cost_in_usd_ticks={ticks}"


def _summarize_image_info(data, requested_model, tag):
    parts = [f"model={data.get('model', requested_model)}"]
    for item in data.get("data", []):
        if "respect_moderation" in item:
            parts.append(f"respect_moderation={item['respect_moderation']}")
        if item.get("revised_prompt"):
            parts.append(f"revised_prompt={item['revised_prompt']}")
    c = _cost_str(data)
    if c:
        parts.append(c)
    return "; ".join(parts)


# --- ffmpeg / media helpers --------------------------------------------------
def _find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _file_to_data_uri(path, mime):
    with open(path, "rb") as f:
        return f"data:{mime};base64,{base64.b64encode(f.read()).decode('ascii')}"


def _empty_frames():
    import numpy as np
    batch = np.zeros((1, 64, 64, 3), dtype="float32")
    try:
        import torch
        return torch.from_numpy(batch)
    except ImportError:
        return batch


def _extract_frames(video_path):
    import numpy as np
    import imageio

    reader = imageio.get_reader(video_path)
    meta = reader.get_meta_data()
    fps = float(meta.get("fps", 24.0))
    arrays = [np.asarray(frame).astype("float32") / 255.0 for frame in reader]
    reader.close()
    if not arrays:
        raise RuntimeError("No frames decoded.")
    batch = np.stack(arrays, axis=0)
    try:
        import torch
        return torch.from_numpy(batch), fps
    except ImportError:
        return batch, fps


def _silent_audio(seconds=1.0, sample_rate=44100):
    import numpy as np
    n = max(1, int(seconds * sample_rate))
    wav = np.zeros((1, 2, n), dtype="float32")
    try:
        import torch
        wav = torch.from_numpy(wav)
    except ImportError:
        pass
    return {"waveform": wav, "sample_rate": sample_rate}


def _extract_audio(video_path, fallback_seconds=1.0):
    """Decode the audio track to a ComfyUI AUDIO dict {waveform[1,C,N], sample_rate}.
    Returns silence when the file has no audio track or ffmpeg is missing."""
    import numpy as np

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        print("[Grok Imagine] ffmpeg not found — returning silent AUDIO.")
        return _silent_audio(fallback_seconds), False
    tmp = tempfile.mkdtemp(prefix="grok_audio_")
    wav_path = os.path.join(tmp, "audio.wav")
    try:
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", video_path, "-vn",
               "-acodec", "pcm_s16le", "-ac", "2", "-ar", "44100", wav_path]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(wav_path):
            print("[Grok Imagine] No audio track decoded — returning silent AUDIO.")
            return _silent_audio(fallback_seconds), False
        with wave.open(wav_path, "rb") as w:
            ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
        data = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
        data = data.reshape(-1, ch).T[None, ...]  # (1, C, N)
        try:
            import torch
            data = torch.from_numpy(np.ascontiguousarray(data))
        except ImportError:
            pass
        return {"waveform": data, "sample_rate": sr}, True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _make_video_object(video_path):
    """Wrap the MP4 as a native ComfyUI VIDEO object (for Save Video, Preview
    Video, Get Video Components...). Returns None on very old ComfyUI builds."""
    try:
        from comfy_api.latest import InputImpl
        return InputImpl.VideoFromFile(video_path)
    except Exception:
        pass
    try:
        from comfy_api.input_impl import VideoFromFile
        return VideoFromFile(video_path)
    except Exception:
        print("[Grok Imagine] Native VIDEO type unavailable in this ComfyUI "
              "version — use the frames/audio/video_path outputs instead.")
        return None


def _submit_and_poll(base, key, endpoint, payload, tag, poll_interval,
                     poll_timeout, submit_timeout=180):
    """Submit a video job, poll /videos/{id} until done, return (result_json, request_id)."""
    submit = _post_json(base + endpoint, key, payload, submit_timeout, tag)
    request_id = submit.get("request_id") or submit.get("id")
    if not request_id:
        raise RuntimeError(f"[{tag}] No request_id in: {str(submit)[:500]}")
    print(f"[{tag}] Submitted (request_id={request_id}). Polling every "
          f"{poll_interval}s, up to {poll_timeout}s...")

    headers = {"Authorization": f"Bearer {key}"}
    deadline = time.time() + poll_timeout
    started = time.time()
    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            r = requests.get(f"{base}/videos/{request_id}", headers=headers, timeout=60)
        except (requests.Timeout, requests.ConnectionError) as e:
            print(f"[{tag}] Poll network error ({e}); retrying...")
            continue
        if r.status_code != 200:
            print(f"[{tag}] Poll HTTP {r.status_code}; retrying...")
            continue
        body = r.json()
        status = body.get("status", "")
        if status == "done":
            print(f"[{tag}] Done after {time.time() - started:.0f}s.")
            return body, request_id
        if status in ("failed", "expired"):
            err = body.get("error") or {}
            raise RuntimeError(f"[{tag}] Request {status}: "
                               f"{err.get('code', '')} {err.get('message', '')} "
                               f"{'' if err else str(body)[:400]}".strip())
        print(f"[{tag}] status={status or 'pending'} ({time.time() - started:.0f}s)...")
    raise RuntimeError(f"[{tag}] Timed out after {poll_timeout}s (request_id={request_id}). "
                       f"Raise poll_timeout_seconds for long jobs.")


def _download_video(result, request_id, tag, prefix="grok_video"):
    video = result.get("video") or {}
    url = video.get("url")
    if not url:
        raise RuntimeError(f"[{tag}] No video url in: {str(result)[:500]}")
    path = os.path.join(_output_dir(), f"{prefix}_{request_id}.mp4")
    with requests.get(url, stream=True, timeout=600) as dl:
        dl.raise_for_status()
        with open(path, "wb") as f:
            for chunk in dl.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"[{tag}] Downloaded -> {path} ({size_mb:.1f} MB)")
    return path, video


def _video_info(result, video, payload_model, request_id):
    parts = [f"model={result.get('model', payload_model)}",
             f"duration={video.get('duration', '?')}",
             f"respect_moderation={video.get('respect_moderation', '?')}",
             f"request_id={request_id}"]
    c = _cost_str(result)
    if c:
        parts.append(c)
    return "; ".join(parts)


def _video_outputs(video_path, extract_frames, extract_audio, duration_hint):
    fps = 24.0
    frames = _empty_frames()
    if extract_frames:
        try:
            frames, fps = _extract_frames(video_path)
        except Exception as e:
            print(f"[Grok Imagine] Frame extraction failed ({e}) — video file is "
                  f"still available at video_path.")
    if extract_audio:
        audio, _ = _extract_audio(video_path, fallback_seconds=float(duration_hint or 1))
    else:
        audio = _silent_audio(float(duration_hint or 1))
    video_obj = _make_video_object(video_path)
    return frames, float(fps), audio, video_obj


# --- tooltips shared by several nodes --------------------------------------
TT_PROMPT = "What to generate. Concrete descriptions (subject, action, setting, camera, light) work best."
TT_CUSTOM_MODEL = ("Type any model ID here to override the dropdown (e.g. a brand-new "
                   "release). Leave empty to use the dropdown. See console.x.ai or "
                   "GET /v1/models for valid IDs.")
TT_API_KEY = ("Your xAI API key. NOT recommended here — it is saved inside exported "
              "workflows. Prefer the XAI_API_KEY environment variable or the key file.")
TT_API_KEY_FILE = ("Text file containing only the key. Searched in the ComfyUI root, "
                   "the portable root and this pack's folder (or give an absolute path). "
                   "The same file serves the Prompt Forge pack.")
TT_BASE_URL = "API base URL. Leave at https://api.x.ai/v1 unless you use a proxy."
TT_TIMEOUT = "Seconds to wait for one HTTP response before giving up."
TT_POLL_INT = "Seconds between status checks while the video renders."
TT_POLL_TO = ("Maximum seconds to wait for the video job. 15-second 1080p clips can "
              "take several minutes — raise this if jobs time out.")
TT_EXTRACT_FRAMES = ("Decode the MP4 into an IMAGE batch on the 'frames' output. Turn off "
                     "to save RAM/time when you only need the file, the VIDEO object or "
                     "the audio.")
TT_EXTRACT_AUDIO = ("Decode the video's audio track to the 'audio' output (AUDIO). Needs "
                    "ffmpeg (bundled with imageio-ffmpeg). Off = silent placeholder.")
TT_ASPECT_IMG = ("Output aspect ratio. 'auto' lets the model pick based on the prompt. "
                 "21:9 = cinematic widescreen, 5:2 = wide banner.")
TT_RES_IMG = "Output size class: 1k is faster and cheaper, 2k has more detail."
TT_QUALITY = ("Quality tier of grok-imagine-image-2.0 (billed per tier). default = do "
              "not send (API picks auto). auto = low for generation, medium for "
              "editing. low = cheapest/fastest. medium = best fidelity. Older image "
              "models ignore or reject this — keep 'default' for them.")
TT_N = "How many images to generate in one request (1-10). Each one is billed."


# ---------------------------------------------------------------------------
# Node 1 — Text-to-Image
# ---------------------------------------------------------------------------
class GrokImagineGenerate:
    CATEGORY = "Grok/Imagine"
    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("images", "prompt", "info")
    FUNCTION = "generate"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip": TT_PROMPT}),
                "model": (IMAGE_MODELS, {"default": IMAGE_MODELS[0], "tooltip":
                    "Image model. grok-imagine-image-2.0 is current (Aurora engine, "
                    "sharp text rendering, quality tiers). grok-imagine-image = 1.0. "
                    "grok-imagine-image-quality is retired on 2026-11-02 and then "
                    "redirects to 2.0 at low quality."}),
                "n": ("INT", {"default": 1, "min": 1, "max": 10, "tooltip": TT_N}),
                "aspect_ratio": (IMAGE_ASPECT_RATIOS, {"default": "auto", "tooltip": TT_ASPECT_IMG}),
                "resolution": (["1k", "2k"], {"default": "1k", "tooltip": TT_RES_IMG}),
                "quality": (IMAGE_QUALITY, {"default": "default", "tooltip": TT_QUALITY}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                                 "control_after_generate": True, "tooltip":
                    "Cache-breaker only (not sent to the API; results are not "
                    "reproducible). randomize = new request every queue run; fixed = "
                    "reuse the cached result for identical settings (no cost)."}),
            },
            "optional": {
                "custom_model": ("STRING", {"default": "", "tooltip": TT_CUSTOM_MODEL}),
                "api_key": ("STRING", {"default": "", "tooltip": TT_API_KEY}),
                "api_key_file": ("STRING", {"default": DEFAULT_KEY_FILE, "tooltip": TT_API_KEY_FILE}),
                "base_url": ("STRING", {"default": DEFAULT_BASE_URL, "tooltip": TT_BASE_URL}),
                "timeout_seconds": ("INT", {"default": 300, "min": 30, "max": 1200, "tooltip": TT_TIMEOUT}),
            },
        }

    def generate(self, prompt, model, n, aspect_ratio, resolution, quality="default",
                 seed=0, custom_model="", api_key="", api_key_file=DEFAULT_KEY_FILE,
                 base_url=DEFAULT_BASE_URL, timeout_seconds=300):
        tag = "Grok Imagine Generate"
        key = _resolve_api_key(api_key, api_key_file)
        model_id = _pick_model(model, custom_model)
        payload = {
            "model": model_id,
            "prompt": prompt,
            "n": int(n),
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "response_format": "b64_json",
        }
        if quality != "default":
            payload["quality"] = quality
        print(f"[{tag}] -> model={model_id} n={n} ratio={aspect_ratio} res={resolution} "
              f"quality={quality}")
        data = _post_json(base_url.rstrip("/") + "/images/generations", key, payload,
                          timeout_seconds, tag)
        items = data.get("data") or []
        if not items:
            raise RuntimeError(f"[{tag}] Empty response: {str(data)[:500]}")
        arrays = [_b64_or_url_to_array(it, timeout_seconds) for it in items]
        info = _summarize_image_info(data, model_id, tag)
        print(f"[{tag}] <- {len(arrays)} image(s). {info}")
        return (_stack_images(arrays), prompt, info)


# ---------------------------------------------------------------------------
# Node 2 — Image Editing (1–5 source images)
# ---------------------------------------------------------------------------
class GrokImagineEdit:
    CATEGORY = "Grok/Imagine"
    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("image", "prompt", "info")
    FUNCTION = "edit"

    @classmethod
    def INPUT_TYPES(cls):
        extra_img = {"tooltip": "Optional additional source image. Order matters: refer "
                                "to them in the prompt as the first, second... image."}
        return {
            "required": {
                "image_1": ("IMAGE", {"tooltip": "Main source image. The output keeps its "
                                                 "aspect ratio unless aspect_ratio overrides it."}),
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip":
                    "Editing instruction, e.g. 'give her a red coat' or 'put the product "
                    "from the second image on the table'."}),
                "model": (IMAGE_MODELS, {"default": IMAGE_MODELS[0], "tooltip":
                    "Image model used for editing (2.0 recommended)."}),
                "aspect_ratio": (["source"] + IMAGE_ASPECT_RATIOS, {"default": "source",
                    "tooltip": "'source' keeps the first input image's ratio; any other "
                               "value re-frames the result."}),
                "quality": (IMAGE_QUALITY, {"default": "default", "tooltip": TT_QUALITY}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                                 "control_after_generate": True, "tooltip":
                    "Cache-breaker only (not sent). randomize = new request each run."}),
            },
            "optional": {
                "image_2": ("IMAGE", extra_img),
                "image_3": ("IMAGE", extra_img),
                "image_4": ("IMAGE", extra_img),
                "image_5": ("IMAGE", extra_img),
                "n": ("INT", {"default": 1, "min": 1, "max": 10, "tooltip": TT_N}),
                "custom_model": ("STRING", {"default": "", "tooltip": TT_CUSTOM_MODEL}),
                "api_key": ("STRING", {"default": "", "tooltip": TT_API_KEY}),
                "api_key_file": ("STRING", {"default": DEFAULT_KEY_FILE, "tooltip": TT_API_KEY_FILE}),
                "base_url": ("STRING", {"default": DEFAULT_BASE_URL, "tooltip": TT_BASE_URL}),
                "timeout_seconds": ("INT", {"default": 300, "min": 30, "max": 1200, "tooltip": TT_TIMEOUT}),
            },
        }

    def edit(self, image_1, prompt, model, aspect_ratio, quality="default", seed=0,
             image_2=None, image_3=None, image_4=None, image_5=None, n=1,
             custom_model="", api_key="", api_key_file=DEFAULT_KEY_FILE,
             base_url=DEFAULT_BASE_URL, timeout_seconds=300):
        tag = "Grok Imagine Edit"
        key = _resolve_api_key(api_key, api_key_file)
        model_id = _pick_model(model, custom_model)

        uris = [_tensor_to_data_uri(image_1)]
        for extra in (image_2, image_3, image_4, image_5):
            if extra is not None:
                uris.append(_tensor_to_data_uri(extra))

        payload = {
            "model": model_id,
            "prompt": prompt,
            "n": int(n),
            "response_format": "b64_json",
        }
        if len(uris) == 1:
            payload["image"] = {"type": "image_url", "url": uris[0]}
        else:
            payload["images"] = [{"type": "image_url", "url": u} for u in uris]
        if aspect_ratio != "source":
            payload["aspect_ratio"] = aspect_ratio
        if quality != "default":
            payload["quality"] = quality

        print(f"[{tag}] -> model={model_id} sources={len(uris)} n={n} quality={quality}")
        data = _post_json(base_url.rstrip("/") + "/images/edits", key, payload,
                          timeout_seconds, tag)
        items = data.get("data") or []
        if not items:
            raise RuntimeError(f"[{tag}] Empty response: {str(data)[:500]}")
        arrays = [_b64_or_url_to_array(it, timeout_seconds) for it in items]
        info = _summarize_image_info(data, model_id, tag)
        print(f"[{tag}] <- {len(arrays)} result(s). {info}")
        return (_stack_images(arrays), prompt, info)


# ---------------------------------------------------------------------------
# Node 3 — Video generation: text / image / reference → video
# ---------------------------------------------------------------------------
VIDEO_RETURN_TYPES = ("IMAGE", "FLOAT", "AUDIO", "VIDEO", "STRING", "STRING", "STRING")
VIDEO_RETURN_NAMES = ("frames", "fps", "audio", "video", "video_path", "prompt", "info")


def _video_common_optional():
    return {
        "extract_frames": ("BOOLEAN", {"default": True, "tooltip": TT_EXTRACT_FRAMES}),
        "extract_audio": ("BOOLEAN", {"default": True, "tooltip": TT_EXTRACT_AUDIO}),
        "custom_model": ("STRING", {"default": "", "tooltip": TT_CUSTOM_MODEL}),
        "api_key": ("STRING", {"default": "", "tooltip": TT_API_KEY}),
        "api_key_file": ("STRING", {"default": DEFAULT_KEY_FILE, "tooltip": TT_API_KEY_FILE}),
        "base_url": ("STRING", {"default": DEFAULT_BASE_URL, "tooltip": TT_BASE_URL}),
        "poll_interval_seconds": ("INT", {"default": 5, "min": 2, "max": 60, "tooltip": TT_POLL_INT}),
        "poll_timeout_seconds": ("INT", {"default": 900, "min": 60, "max": 7200, "tooltip": TT_POLL_TO}),
    }


class GrokImagineVideo:
    CATEGORY = "Grok/Imagine"
    RETURN_TYPES = VIDEO_RETURN_TYPES
    RETURN_NAMES = VIDEO_RETURN_NAMES
    FUNCTION = "generate"

    @classmethod
    def INPUT_TYPES(cls):
        opt = {
            "image": ("IMAGE", {"tooltip":
                "Connect for IMAGE-TO-VIDEO: this picture becomes the first frame and "
                "the prompt describes the motion. Leave unconnected for text-to-video. "
                "Cannot be combined with reference_images."}),
            "reference_images": ("IMAGE", {"tooltip":
                "Connect an IMAGE batch (up to 7) for REFERENCE-TO-VIDEO: people, "
                "objects or clothing from these pictures appear in the video without "
                "locking the first frame. Refer to them in the prompt as <IMAGE_1>, "
                "<IMAGE_2>... Use a Batch Images node to combine several pictures. "
                "grok-imagine-video-1.5 only, max 720p. Cannot be combined with image."}),
            "voice_ids": ("STRING", {"default": "", "tooltip":
                "Optional preset voices for reference-to-video (grok-imagine-video-1.5), "
                "comma-separated, max 3, e.g. 'eve, leo'. Refer to them in the prompt as "
                "<AUDIO_0>, <AUDIO_1>... Same roster as xAI Text-to-Speech. Leave empty "
                "for no voice reference."}),
            "generate_audio": ("BOOLEAN", {"default": True, "tooltip":
                "On = the model generates a soundtrack/voices (default). Off = silent "
                "video (sends generate_audio=false)."}),
            "image_max_side": ("INT", {"default": 2048, "min": 256, "max": 4096, "step": 64,
                "tooltip": "Input images are downscaled so their longer side is at most "
                           "this many pixels before upload (saves upload time; the API "
                           "limit is 20 MiB per image)."}),
        }
        opt.update(_video_common_optional())
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip":
                    "Describe the scene and the motion. For image-to-video describe what "
                    "should happen; for reference-to-video mention <IMAGE_1>... and "
                    "<AUDIO_0>... explicitly."}),
                "model": (VIDEO_MODELS, {"default": VIDEO_MODELS[0], "tooltip":
                    "grok-imagine-video-1.5: text/image/reference-to-video, 1080p, up to "
                    "15 s, audio, preset voices. grok-imagine-video (1.0): older, cheaper "
                    "per second, needed for video editing."}),
                "duration": ("INT", {"default": 6, "min": 1, "max": 15, "tooltip":
                    "Length in seconds (1-15). Billing is per second and rises with "
                    "resolution."}),
                "aspect_ratio": (VIDEO_ASPECT_RATIOS, {"default": "default", "tooltip":
                    "'default' = 16:9 for text-to-video, the input image's ratio for "
                    "image-to-video. Any explicit value overrides (and stretches an "
                    "input image if it does not match)."}),
                "resolution": (VIDEO_RESOLUTIONS, {"default": "720p", "tooltip":
                    "480p (API default, fastest), 720p, or 1080p (grok-imagine-video-1.5 "
                    "text/image-to-video only; reference-to-video is capped at 720p). "
                    "'default' = do not send."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                                 "control_after_generate": True, "tooltip":
                    "Cache-breaker only (not sent). randomize = new video every run; "
                    "fixed = re-use the cached result for identical settings (no cost)."}),
            },
            "optional": opt,
        }

    def generate(self, prompt, model, duration, aspect_ratio, resolution, seed=0,
                 image=None, reference_images=None, voice_ids="", generate_audio=True,
                 image_max_side=2048, extract_frames=True, extract_audio=True,
                 custom_model="", api_key="", api_key_file=DEFAULT_KEY_FILE,
                 base_url=DEFAULT_BASE_URL, poll_interval_seconds=5,
                 poll_timeout_seconds=900):
        tag = "Grok Imagine Video"
        key = _resolve_api_key(api_key, api_key_file)
        base = base_url.rstrip("/")
        model_id = _pick_model(model, custom_model)

        if image is not None and reference_images is not None:
            raise RuntimeError(f"[{tag}] Connect EITHER 'image' (image-to-video) OR "
                               f"'reference_images' (reference-to-video), not both — "
                               f"the API rejects that combination.")

        payload = {"model": model_id, "prompt": prompt, "duration": int(duration)}
        if aspect_ratio != "default":
            payload["aspect_ratio"] = aspect_ratio
        if resolution != "default":
            payload["resolution"] = resolution
        if not generate_audio:
            payload["generate_audio"] = False

        mode = "text-to-video"
        if image is not None:
            payload["image"] = {"url": _tensor_to_data_uri(image, max_side=image_max_side, fmt="png")}
            mode = "image-to-video"
        if reference_images is not None:
            uris = _batch_to_data_uris(reference_images, 7, max_side=image_max_side, fmt="png")
            payload["reference_images"] = [{"url": u} for u in uris]
            mode = f"reference-to-video ({len(uris)} image(s))"
        voices = [v.strip() for v in (voice_ids or "").split(",") if v.strip()][:3]
        if voices:
            payload["reference_audios"] = [{"voice_id": v} for v in voices]
            mode += f" + voices {voices}"
            if reference_images is None and image is None:
                mode = f"reference-to-video (voices {voices})"
        if resolution == "1080p" and reference_images is not None:
            print(f"[{tag}] Note: reference-to-video is capped at 720p by the API.")

        print(f"[{tag}] -> model={model_id} mode={mode} duration={duration}s "
              f"ratio={aspect_ratio} res={resolution} audio={generate_audio}")
        result, request_id = _submit_and_poll(base, key, "/videos/generations", payload,
                                              tag, poll_interval_seconds,
                                              poll_timeout_seconds)
        video_path, video = _download_video(result, request_id, tag)
        info = _video_info(result, video, model_id, request_id)
        print(f"[{tag}] <- {info}")
        frames, fps, audio, video_obj = _video_outputs(video_path, extract_frames,
                                                       extract_audio,
                                                       video.get("duration", duration))
        return (frames, fps, audio, video_obj, video_path, prompt, info)


# ---------------------------------------------------------------------------
# Node 4 — Video Edit (modify an existing video)
# ---------------------------------------------------------------------------
class GrokImagineVideoEdit:
    CATEGORY = "Grok/Imagine"
    RETURN_TYPES = VIDEO_RETURN_TYPES
    RETURN_NAMES = VIDEO_RETURN_NAMES
    FUNCTION = "edit"

    @classmethod
    def INPUT_TYPES(cls):
        opt = {
            "video_url": ("STRING", {"default": "", "tooltip":
                "Alternative to video_path: a PUBLIC https URL of an .mp4 (max 8.7 s). "
                "Used only when video_path is empty."}),
        }
        opt.update(_video_common_optional())
        return {
            "required": {
                "video_path": ("STRING", {"default": "", "tooltip":
                    "Local .mp4 to edit (H.264/H.265/AV1), max 8.7 seconds. Connect the "
                    "video_path output of a Grok video node or type a path. The file is "
                    "uploaded inline (base64)."}),
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip":
                    "What to change, e.g. 'give the woman a silver necklace'. The rest of "
                    "the video is preserved."}),
                "model": (VIDEO_MODELS, {"default": "grok-imagine-video", "tooltip":
                    "Video editing is documented for grok-imagine-video (1.0). Output keeps "
                    "the input's duration and aspect ratio, capped at 720p."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                                 "control_after_generate": True, "tooltip":
                    "Cache-breaker only (not sent)."}),
            },
            "optional": opt,
        }

    def edit(self, video_path, prompt, model, seed=0, video_url="", extract_frames=True,
             extract_audio=True, custom_model="", api_key="",
             api_key_file=DEFAULT_KEY_FILE, base_url=DEFAULT_BASE_URL,
             poll_interval_seconds=5, poll_timeout_seconds=900):
        tag = "Grok Imagine Video Edit"
        key = _resolve_api_key(api_key, api_key_file)
        base = base_url.rstrip("/")
        model_id = _pick_model(model, custom_model)
        src = _video_source(video_path, video_url, tag)
        payload = {"model": model_id, "prompt": prompt, "video": src}
        print(f"[{tag}] -> model={model_id}")
        result, request_id = _submit_and_poll(base, key, "/videos/edits", payload, tag,
                                              poll_interval_seconds, poll_timeout_seconds,
                                              submit_timeout=600)
        out_path, video = _download_video(result, request_id, tag, prefix="grok_edit")
        info = _video_info(result, video, model_id, request_id)
        print(f"[{tag}] <- {info}")
        frames, fps, audio, video_obj = _video_outputs(out_path, extract_frames,
                                                       extract_audio, video.get("duration", 5))
        return (frames, fps, audio, video_obj, out_path, prompt, info)


def _video_source(video_path, video_url, tag):
    p = (video_path or "").strip().strip('"')
    if p:
        if not os.path.isfile(p):
            raise RuntimeError(f"[{tag}] video_path not found: {p}")
        if not p.lower().endswith(".mp4"):
            raise RuntimeError(f"[{tag}] The API requires an .mp4 file: {p}")
        size_mb = os.path.getsize(p) / (1024 * 1024)
        print(f"[{tag}] Uploading {os.path.basename(p)} ({size_mb:.1f} MB) inline...")
        return {"url": _file_to_data_uri(p, "video/mp4")}
    u = (video_url or "").strip()
    if u:
        return {"url": u}
    raise RuntimeError(f"[{tag}] Provide video_path (local .mp4) or video_url.")


# ---------------------------------------------------------------------------
# Node 5 — Video Extend (continue from the last frame)
# ---------------------------------------------------------------------------
class GrokImagineVideoExtend:
    CATEGORY = "Grok/Imagine"
    RETURN_TYPES = VIDEO_RETURN_TYPES
    RETURN_NAMES = VIDEO_RETURN_NAMES
    FUNCTION = "extend"

    @classmethod
    def INPUT_TYPES(cls):
        opt = {
            "video_url": ("STRING", {"default": "", "tooltip":
                "Alternative to video_path: a PUBLIC https URL of an .mp4. Used only "
                "when video_path is empty."}),
        }
        opt.update(_video_common_optional())
        return {
            "required": {
                "video_path": ("STRING", {"default": "", "tooltip":
                    "Local .mp4 to continue. Connect the video_path output of a Grok "
                    "video node or type a path. Uploaded inline (base64)."}),
                "prompt": ("STRING", {"multiline": True, "default": "", "tooltip":
                    "What happens next, starting from the last frame."}),
                "model": (VIDEO_MODELS, {"default": VIDEO_MODELS[0], "tooltip":
                    "Video model for the extension."}),
                "duration": ("INT", {"default": 5, "min": 1, "max": 15, "tooltip":
                    "Seconds to ADD. A 10 s input with duration=5 returns a 15 s video. "
                    "Output resolution matches the input, capped at 720p."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                                 "control_after_generate": True, "tooltip":
                    "Cache-breaker only (not sent)."}),
            },
            "optional": opt,
        }

    def extend(self, video_path, prompt, model, duration, seed=0, video_url="",
               extract_frames=True, extract_audio=True, custom_model="", api_key="",
               api_key_file=DEFAULT_KEY_FILE, base_url=DEFAULT_BASE_URL,
               poll_interval_seconds=5, poll_timeout_seconds=900):
        tag = "Grok Imagine Video Extend"
        key = _resolve_api_key(api_key, api_key_file)
        base = base_url.rstrip("/")
        model_id = _pick_model(model, custom_model)
        src = _video_source(video_path, video_url, tag)
        payload = {"model": model_id, "prompt": prompt, "video": src,
                   "duration": int(duration)}
        print(f"[{tag}] -> model={model_id} +{duration}s")
        result, request_id = _submit_and_poll(base, key, "/videos/extensions", payload,
                                              tag, poll_interval_seconds,
                                              poll_timeout_seconds, submit_timeout=600)
        out_path, video = _download_video(result, request_id, tag, prefix="grok_extend")
        info = _video_info(result, video, model_id, request_id)
        print(f"[{tag}] <- {info}")
        frames, fps, audio, video_obj = _video_outputs(out_path, extract_frames,
                                                       extract_audio, video.get("duration", duration))
        return (frames, fps, audio, video_obj, out_path, prompt, info)


# ---------------------------------------------------------------------------
# Node 6 — Save video to any folder on any drive with a custom filename
# ---------------------------------------------------------------------------
class GrokSaveVideo:
    CATEGORY = "Grok/Imagine"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("saved_path",)
    FUNCTION = "save"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_path": ("STRING", {"forceInput": True, "tooltip":
                    "Connect the video_path output of a Grok video node (the downloaded "
                    "MP4 in ComfyUI/output/grok_imagine)."}),
                "output_dir": ("STRING", {"default": "D:/grok_videos", "tooltip":
                    "Destination folder on any drive, e.g. O:\\projects\\clips. Created "
                    "if missing."}),
                "filename": ("STRING", {"default": "my_video", "tooltip":
                    "File name without extension (.mp4 is added). Illegal characters "
                    "are replaced by '_'."}),
            },
            "optional": {
                "overwrite": ("BOOLEAN", {"default": False, "tooltip":
                    "Off: an existing name gets _001, _002... appended. On: replace."}),
                "prompt_text": ("STRING", {"default": "", "forceInput": True, "tooltip":
                    "Connect the 'prompt' output of the video node to also write "
                    "<filename>_prompt.txt next to the video."}),
                "save_prompt_txt": ("BOOLEAN", {"default": True, "tooltip":
                    "Write <filename>_prompt.txt when prompt_text is connected."}),
                "save_workflow_json": ("BOOLEAN", {"default": True, "tooltip":
                    "Write <filename>_workflow.json (the full ComfyUI workflow, loadable "
                    "via drag & drop) next to the video."}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    def save(self, video_path, output_dir, filename, overwrite=False, prompt_text="",
             save_prompt_txt=True, save_workflow_json=True, prompt=None,
             extra_pnginfo=None):
        tag = "Grok Save Video"
        src = video_path.strip().strip('"')
        if not os.path.isfile(src):
            raise RuntimeError(f"[{tag}] Source not found: {src}")

        out_dir = output_dir.strip().strip('"')
        os.makedirs(out_dir, exist_ok=True)

        name = re.sub(r'[<>:"/\\|?*]', "_", filename.strip()) or "grok_video"
        ext = os.path.splitext(src)[1] or ".mp4"
        if name.lower().endswith(ext.lower()):
            name = name[:-len(ext)]

        stem = name
        dest = os.path.join(out_dir, stem + ext)
        if os.path.exists(dest) and not overwrite:
            i = 1
            while os.path.exists(os.path.join(out_dir, f"{name}_{i:03d}{ext}")):
                i += 1
            stem = f"{name}_{i:03d}"
            dest = os.path.join(out_dir, stem + ext)

        shutil.copy2(src, dest)
        print(f"[{tag}] Saved -> {dest}")

        if save_prompt_txt and (prompt_text or "").strip():
            p = os.path.join(out_dir, f"{stem}_prompt.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write(prompt_text.strip() + "\n")
            print(f"[{tag}] Prompt -> {p}")

        if save_workflow_json:
            wf = None
            if isinstance(extra_pnginfo, dict):
                wf = extra_pnginfo.get("workflow")
            if wf is not None or prompt is not None:
                p = os.path.join(out_dir, f"{stem}_workflow.json")
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(wf if wf is not None else {"prompt": prompt}, f,
                              indent=2, ensure_ascii=False)
                print(f"[{tag}] Workflow -> {p}")
        return {"ui": {"text": [f"Saved: {dest}"]}, "result": (dest,)}


NODE_CLASS_MAPPINGS = {
    "GrokImagineGenerate": GrokImagineGenerate,
    "GrokImagineEdit": GrokImagineEdit,
    "GrokImagineVideo": GrokImagineVideo,
    "GrokImagineVideoEdit": GrokImagineVideoEdit,
    "GrokImagineVideoExtend": GrokImagineVideoExtend,
    "GrokSaveVideo": GrokSaveVideo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "GrokImagineGenerate": "Grok Imagine Generate (xAI)",
    "GrokImagineEdit": "Grok Imagine Edit 1-5 Images (xAI)",
    "GrokImagineVideo": "Grok Imagine Video (xAI)",
    "GrokImagineVideoEdit": "Grok Imagine Video Edit (xAI)",
    "GrokImagineVideoExtend": "Grok Imagine Video Extend (xAI)",
    "GrokSaveVideo": "Grok Save Video To Path",
}
