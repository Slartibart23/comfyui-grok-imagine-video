"""
Grok Imagine — ComfyUI custom nodes
Image generation, multi-image editing, and video generation via the official
xAI Grok Imagine API. Universal nodes, OpenAI-compatible endpoints.

License: MIT
"""

import base64
import io
import os
import re
import shutil
import time

import requests

# ---------------------------------------------------------------------------
# MODEL LISTS — edit these when xAI ships new models, then restart ComfyUI.
# The `custom_model` field on each node overrides the dropdown when non-empty.
# ---------------------------------------------------------------------------
IMAGE_MODELS = [
    "grok-imagine-image-quality",
    "grok-imagine-image-fast",
    "grok-imagine-image-pro",
]
VIDEO_MODELS = [
    "grok-imagine-video",
    "grok-imagine-video-1.5",
]

ASPECT_RATIOS = ["auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3",
                 "2:1", "1:2", "19.5:9", "9:19.5", "20:9", "9:20"]

DEFAULT_BASE_URL = "https://api.x.ai/v1"


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


def _resolve_api_key(api_key_widget: str, api_key_file: str) -> str:
    if api_key_widget and api_key_widget.strip():
        return api_key_widget.strip()
    env_key = os.environ.get("XAI_API_KEY", "").strip()
    if env_key:
        return env_key
    if api_key_file and api_key_file.strip():
        p = api_key_file.strip()
        candidates = [p] if os.path.isabs(p) else [
            os.path.join(_find_comfy_root(), p),
            os.path.join(os.path.dirname(_find_comfy_root()), p),
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


def _pick_model(dropdown: str, custom_model: str) -> str:
    return custom_model.strip() if custom_model and custom_model.strip() else dropdown


def _tensor_to_data_uri(image, image_index=0, max_side=2048, fmt="jpeg"):
    import numpy as np
    from PIL import Image

    arr = image
    if hasattr(arr, "cpu"):
        arr = arr.cpu().numpy()
    arr = np.asarray(arr)
    if arr.ndim == 4:
        arr = arr[min(int(image_index), arr.shape[0] - 1)]
    arr = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    if fmt == "png":
        img.save(buf, format="PNG")
        mime = "image/png"
    else:
        img.convert("RGB").save(buf, format="JPEG", quality=95)
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"


def _b64_or_url_to_tensor(item, timeout=120):
    """Convert one API image result (b64_json or url) to a numpy float array."""
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
    """Stack HxWxC arrays into a (B,H,W,C) tensor; pad-crop to first image size
    if the API returns mixed sizes. Returns torch tensor if torch is available."""
    import numpy as np

    h, w = arrays[0].shape[:2]
    fixed = []
    for a in arrays:
        if a.shape[:2] != (h, w):
            from PIL import Image
            img = Image.fromarray((a * 255).astype("uint8")).resize((w, h),
                                                                    Image.LANCZOS)
            a = np.asarray(img).astype("float32") / 255.0
        fixed.append(a)
    batch = np.stack(fixed, axis=0)
    try:
        import torch
        return torch.from_numpy(batch)
    except ImportError:
        return batch


def _post_json(url, key, payload, timeout, tag):
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last_error = None
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = RuntimeError(f"[{tag}] HTTP {resp.status_code}: "
                                          f"{resp.text[:300]}")
                time.sleep(3 * (attempt + 1))
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"[{tag}] HTTP {resp.status_code}: {resp.text[:500]}")
            return resp.json()
        except (requests.Timeout, requests.ConnectionError) as e:
            last_error = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"[{tag}] Request failed after retries: {last_error}")


def _summarize_info(data, tag):
    parts = []
    if "model" in data:
        parts.append(f"model={data['model']}")
    for item in data.get("data", []):
        if "respect_moderation" in item:
            parts.append(f"respect_moderation={item['respect_moderation']}")
        if item.get("revised_prompt"):
            parts.append(f"revised_prompt={item['revised_prompt']}")
    extra = {k: v for k, v in data.items() if k not in ("data",)}
    info = "; ".join(parts) if parts else ""
    return (info + (f" | raw_meta={extra}" if extra else "")).strip() or f"[{tag}] ok"


# ---------------------------------------------------------------------------
# Node 1 — Text-to-Image
# ---------------------------------------------------------------------------
class GrokImagineGenerate:
    CATEGORY = "Grok/Imagine"
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "info")
    FUNCTION = "generate"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "model": (IMAGE_MODELS, {"default": IMAGE_MODELS[0]}),
                "n": ("INT", {"default": 1, "min": 1, "max": 10}),
                "aspect_ratio": (ASPECT_RATIOS, {"default": "auto"}),
                "resolution": (["1k", "2k"], {"default": "1k"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                    "control_after_generate": True, "tooltip":
                    "Cache-breaker: change/randomize to force a new API call. "
                    "Not sent to the API (no documented seed support)."}),
            },
            "optional": {
                "custom_model": ("STRING", {"default": "", "tooltip":
                    "Overrides the model dropdown when non-empty."}),
                "api_key": ("STRING", {"default": ""}),
                "api_key_file": ("STRING", {"default": "grok_api_key.txt"}),
                "base_url": ("STRING", {"default": DEFAULT_BASE_URL}),
                "timeout_seconds": ("INT", {"default": 300, "min": 30, "max": 1200}),
            },
        }

    def generate(self, prompt, model, n, aspect_ratio, resolution, seed=0,
                 custom_model="", api_key="", api_key_file="grok_api_key.txt",
                 base_url=DEFAULT_BASE_URL, timeout_seconds=300):
        key = _resolve_api_key(api_key, api_key_file)
        payload = {
            "model": _pick_model(model, custom_model),
            "prompt": prompt,
            "n": int(n),
            "resolution": resolution,
            "response_format": "b64_json",
        }
        if aspect_ratio != "auto":
            payload["aspect_ratio"] = aspect_ratio
        else:
            payload["aspect_ratio"] = "auto"

        data = _post_json(base_url.rstrip("/") + "/images/generations", key,
                          payload, timeout_seconds, "Grok Imagine Generate")
        items = data.get("data") or []
        if not items:
            raise RuntimeError(f"[Grok Imagine Generate] Empty response: {str(data)[:500]}")
        arrays = [_b64_or_url_to_tensor(it, timeout_seconds) for it in items]
        info = _summarize_info(data, "Grok Imagine Generate")
        print(f"[Grok Imagine Generate] {len(arrays)} image(s). {info}")
        return (_stack_images(arrays), info)


# ---------------------------------------------------------------------------
# Node 2 — Image Editing (1–3 source images)
# ---------------------------------------------------------------------------
class GrokImagineEdit:
    CATEGORY = "Grok/Imagine"
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("image", "info")
    FUNCTION = "edit"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image_1": ("IMAGE",),
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "model": (IMAGE_MODELS, {"default": IMAGE_MODELS[0]}),
                "aspect_ratio": (["source"] + ASPECT_RATIOS, {"default": "source",
                    "tooltip": "'source' keeps the first input image's ratio."}),
                "resolution": (["default", "1k", "2k"], {"default": "default",
                    "tooltip": "'default' omits the parameter (API default)."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                    "control_after_generate": True, "tooltip":
                    "Cache-breaker: change/randomize to force a new API call. "
                    "Not sent to the API (no documented seed support)."}),
            },
            "optional": {
                "image_2": ("IMAGE",),
                "image_3": ("IMAGE",),
                "n": ("INT", {"default": 1, "min": 1, "max": 10}),
                "custom_model": ("STRING", {"default": ""}),
                "api_key": ("STRING", {"default": ""}),
                "api_key_file": ("STRING", {"default": "grok_api_key.txt"}),
                "base_url": ("STRING", {"default": DEFAULT_BASE_URL}),
                "timeout_seconds": ("INT", {"default": 300, "min": 30, "max": 1200}),
            },
        }

    def edit(self, image_1, prompt, model, aspect_ratio, resolution="default", seed=0,
             image_2=None, image_3=None, n=1, custom_model="",
             api_key="", api_key_file="grok_api_key.txt",
             base_url=DEFAULT_BASE_URL, timeout_seconds=300):
        key = _resolve_api_key(api_key, api_key_file)

        uris = [_tensor_to_data_uri(image_1)]
        for extra in (image_2, image_3):
            if extra is not None:
                uris.append(_tensor_to_data_uri(extra))

        payload = {
            "model": _pick_model(model, custom_model),
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
        if resolution != "default":
            payload["resolution"] = resolution

        data = _post_json(base_url.rstrip("/") + "/images/edits", key,
                          payload, timeout_seconds, "Grok Imagine Edit")
        items = data.get("data") or []
        if not items:
            raise RuntimeError(f"[Grok Imagine Edit] Empty response: {str(data)[:500]}")
        arrays = [_b64_or_url_to_tensor(it, timeout_seconds) for it in items]
        info = _summarize_info(data, "Grok Imagine Edit")
        print(f"[Grok Imagine Edit] {len(uris)} source image(s) -> "
              f"{len(arrays)} result(s). {info}")
        return (_stack_images(arrays), info)


# ---------------------------------------------------------------------------
# Node 3 — Video generation (text-to-video / image-to-video), async polling
# ---------------------------------------------------------------------------
class GrokImagineVideo:
    CATEGORY = "Grok/Imagine"
    RETURN_TYPES = ("IMAGE", "FLOAT", "STRING", "STRING")
    RETURN_NAMES = ("frames", "fps", "video_path", "info")
    FUNCTION = "generate"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "model": (VIDEO_MODELS, {"default": VIDEO_MODELS[0]}),
                "duration": ("INT", {"default": 6, "min": 1, "max": 30}),
                "aspect_ratio": (["default"] + ASPECT_RATIOS, {"default": "default"}),
                "resolution": (["default", "480p", "720p", "1080p"],
                               {"default": "default", "tooltip":
                                "1080p only on grok-imagine-video-1.5 image-to-video."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff,
                    "control_after_generate": True, "tooltip":
                    "Cache-breaker: change/randomize to force a new API call. "
                    "Not sent to the API (no documented seed support)."}),
            },
            "optional": {
                "image": ("IMAGE", {"tooltip":
                    "Connect for image-to-video; leave empty for text-to-video."}),
                "extract_frames": ("BOOLEAN", {"default": True}),
                "custom_model": ("STRING", {"default": ""}),
                "api_key": ("STRING", {"default": ""}),
                "api_key_file": ("STRING", {"default": "grok_api_key.txt"}),
                "base_url": ("STRING", {"default": DEFAULT_BASE_URL}),
                "poll_interval_seconds": ("INT", {"default": 5, "min": 2, "max": 60}),
                "poll_timeout_seconds": ("INT", {"default": 600, "min": 60,
                                                 "max": 3600}),
            },
        }

    @staticmethod
    def _empty_frames():
        import numpy as np
        batch = np.zeros((1, 64, 64, 3), dtype="float32")
        try:
            import torch
            return torch.from_numpy(batch)
        except ImportError:
            return batch

    def generate(self, prompt, model, duration, aspect_ratio, resolution, seed=0,
                 image=None, extract_frames=True, custom_model="",
                 api_key="", api_key_file="grok_api_key.txt",
                 base_url=DEFAULT_BASE_URL, poll_interval_seconds=5,
                 poll_timeout_seconds=600):
        key = _resolve_api_key(api_key, api_key_file)
        base = base_url.rstrip("/")

        payload = {
            "model": _pick_model(model, custom_model),
            "prompt": prompt,
            "duration": int(duration),
        }
        if aspect_ratio != "default":
            payload["aspect_ratio"] = aspect_ratio
        if resolution != "default":
            payload["resolution"] = resolution
        if image is not None:
            payload["image"] = {"url": _tensor_to_data_uri(image, fmt="png")}

        submit = _post_json(base + "/videos/generations", key, payload,
                            120, "Grok Imagine Video")
        request_id = submit.get("request_id") or submit.get("id")
        if not request_id:
            raise RuntimeError(f"[Grok Imagine Video] No request_id in: "
                               f"{str(submit)[:500]}")
        print(f"[Grok Imagine Video] Submitted, request_id={request_id}. Polling...")

        headers = {"Authorization": f"Bearer {key}"}
        deadline = time.time() + poll_timeout_seconds
        result = None
        while time.time() < deadline:
            time.sleep(poll_interval_seconds)
            r = requests.get(f"{base}/videos/{request_id}", headers=headers,
                             timeout=60)
            if r.status_code != 200:
                print(f"[Grok Imagine Video] Poll HTTP {r.status_code}, retrying...")
                continue
            body = r.json()
            status = body.get("status", "")
            if status == "done":
                result = body
                break
            if status in ("failed", "expired"):
                raise RuntimeError(f"[Grok Imagine Video] Request {status}: "
                                   f"{str(body)[:500]}")
            print(f"[Grok Imagine Video] status={status or 'pending'}...")
        if result is None:
            raise RuntimeError(f"[Grok Imagine Video] Timed out after "
                               f"{poll_timeout_seconds}s (request_id={request_id}).")

        video = result.get("video") or {}
        video_url = video.get("url")
        if not video_url:
            raise RuntimeError(f"[Grok Imagine Video] No video url in: "
                               f"{str(result)[:500]}")

        # Download the temporary URL immediately
        out_dir = os.path.join(_find_comfy_root(), "output", "grok_imagine")
        os.makedirs(out_dir, exist_ok=True)
        video_path = os.path.join(out_dir, f"grok_video_{request_id}.mp4")
        with requests.get(video_url, stream=True, timeout=300) as dl:
            dl.raise_for_status()
            with open(video_path, "wb") as f:
                for chunk in dl.iter_content(chunk_size=1 << 20):
                    f.write(chunk)

        info = (f"model={result.get('model', payload['model'])}; "
                f"duration={video.get('duration', duration)}; "
                f"respect_moderation={video.get('respect_moderation', '?')}; "
                f"request_id={request_id}")
        print(f"[Grok Imagine Video] Done -> {video_path}. {info}")

        fps = 24.0
        frames = self._empty_frames()
        if extract_frames:
            try:
                frames, fps = self._extract_frames(video_path)
            except Exception as e:
                print(f"[Grok Imagine Video] Frame extraction failed ({e}) — "
                      f"video file is still available at video_path.")
        return (frames, float(fps), video_path, info)

    @staticmethod
    def _extract_frames(video_path):
        import numpy as np
        import imageio
        reader = imageio.get_reader(video_path)
        meta = reader.get_meta_data()
        fps = float(meta.get("fps", 24.0))
        arrays = []
        for frame in reader:
            arrays.append(np.asarray(frame).astype("float32") / 255.0)
        reader.close()
        if not arrays:
            raise RuntimeError("No frames decoded.")
        batch = np.stack(arrays, axis=0)
        try:
            import torch
            return torch.from_numpy(batch), fps
        except ImportError:
            return batch, fps


# ---------------------------------------------------------------------------
# Node 4 — Save video to any folder on any drive with a custom filename
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
                "video_path": ("STRING", {"forceInput": True}),
                "output_dir": ("STRING", {"default": "D:/grok_videos"}),
                "filename": ("STRING", {"default": "my_video"}),
            },
            "optional": {
                "overwrite": ("BOOLEAN", {"default": False, "tooltip":
                    "Off: an existing name gets _001, _002... appended."}),
            },
        }

    def save(self, video_path, output_dir, filename, overwrite=False):
        src = video_path.strip().strip('"')
        if not os.path.isfile(src):
            raise RuntimeError(f"[Grok Save Video] Source not found: {src}")

        out_dir = output_dir.strip().strip('"')
        os.makedirs(out_dir, exist_ok=True)

        name = re.sub(r'[<>:"/\\|?*]', "_", filename.strip()) or "grok_video"
        ext = os.path.splitext(src)[1] or ".mp4"
        if not name.lower().endswith(ext.lower()):
            name += ext

        dest = os.path.join(out_dir, name)
        if os.path.exists(dest) and not overwrite:
            stem, e = os.path.splitext(name)
            i = 1
            while os.path.exists(os.path.join(out_dir, f"{stem}_{i:03d}{e}")):
                i += 1
            dest = os.path.join(out_dir, f"{stem}_{i:03d}{e}")

        shutil.copy2(src, dest)
        print(f"[Grok Save Video] Saved -> {dest}")
        return (dest,)


NODE_CLASS_MAPPINGS = {
    "GrokImagineGenerate": GrokImagineGenerate,
    "GrokImagineEdit": GrokImagineEdit,
    "GrokImagineVideo": GrokImagineVideo,
    "GrokSaveVideo": GrokSaveVideo,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "GrokImagineGenerate": "Grok Imagine Generate (xAI)",
    "GrokImagineEdit": "Grok Imagine Edit 1-3 Images (xAI)",
    "GrokImagineVideo": "Grok Imagine Video (xAI)",
    "GrokSaveVideo": "Grok Save Video To Path",
}
