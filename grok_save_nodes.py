"""
Grok Imagine — save nodes, previews and node buttons.

GrokVideoSave  : moves the MP4 a Grok video node downloaded (temp folder) to a
                 folder + filename of your choice - no re-encoding - and writes
                 optional companions (prompt .txt, workflow .json, base image
                 .jpg). Numbered name_000000, name_000001, ... never overwrites.
GrokImageSave  : same idea for IMAGE batches from Generate / Edit.

Both show a preview in the node and offer buttons: Reveal in Explorer, Open,
Save Last Frame (video), Delete. The buttons only ever act on files this
ComfyUI session saved - never on paths sent from the browser.

Nothing here writes into the ComfyUI output folder.
License: MIT
"""

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from typing import Optional

import numpy as np

LOG = "[Grok Save]"

# --------------------------------------------------------------------------- #
# Session state (per ComfyUI process)
# --------------------------------------------------------------------------- #
_LOCK = threading.Lock()
_FILES: dict[str, str] = {}          # token -> absolute path (served for previews)
_SESSION: dict[str, dict] = {}       # node_id -> last saved set of that node


def register_preview_file(path: str) -> str:
    """Make a local file available to the browser preview. Returns a token."""
    token = uuid.uuid4().hex
    with _LOCK:
        _FILES[token] = os.path.abspath(path)
    return token


def _file_for_token(token: str) -> Optional[str]:
    with _LOCK:
        return _FILES.get(token)


def _remember(node_id, entry: dict):
    with _LOCK:
        _SESSION[str(node_id)] = entry


def _entry(node_id) -> Optional[dict]:
    with _LOCK:
        return _SESSION.get(str(node_id))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _apply_placeholders(name: str) -> str:
    now = datetime.now()

    def date_repl(m):
        fmt = m.group(1)
        for k, v in (("yyyy", "%Y"), ("yy", "%y"), ("MM", "%m"), ("dd", "%d"),
                     ("hh", "%H"), ("mm", "%M"), ("ss", "%S")):
            fmt = fmt.replace(k, v)
        return now.strftime(fmt)

    s = re.sub(r"%date:([^%]+)%", date_repl, name)
    s = s.replace("%date%", now.strftime("%Y-%m-%d"))
    s = s.replace("%time%", now.strftime("%H%M%S"))
    return s


def _sanitize(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "")).strip().rstrip(". ")
    return name or "grok"


def _resolve_folder(folder: str, tag: str) -> str:
    f = (folder or "").strip().strip('"')
    if not f:
        raise RuntimeError(f"{tag} 'folder' is empty. Enter a destination folder, "
                           f"e.g. D:\\Videos\\grok - nothing is ever saved to the "
                           f"ComfyUI output folder by this node.")
    f = os.path.abspath(os.path.expanduser(f))
    os.makedirs(f, exist_ok=True)
    return f


def _next_numbered(folder: str, name: str, ext: str) -> str:
    """name_000000, name_000001, ... continues after the highest number that
    already exists for this name in the folder (any extension). Never reuses."""
    pat = re.compile(re.escape(name) + r"_(\d{6})(?:[._]|$)")
    highest = -1
    for f in os.listdir(folder):
        m = pat.match(f)
        if m:
            highest = max(highest, int(m.group(1)))
    n = highest + 1
    while os.path.exists(os.path.join(folder, f"{name}_{n:06d}{ext}")):
        n += 1
    return f"{name}_{n:06d}"


def _tensor_to_uint8(frame) -> np.ndarray:
    arr = frame
    if hasattr(arr, "detach"):
        arr = arr.detach().cpu().numpy()
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[:, :, None]
    if arr.shape[2] == 1:
        arr = np.repeat(arr, 3, axis=2)
    elif arr.shape[2] > 3:
        arr = arr[:, :, :3]
    return (arr * 255.0).clip(0, 255).astype(np.uint8)


def _save_image_array(arr: np.ndarray, path: str, fmt: str, quality: int = 95,
                      pnginfo=None):
    from PIL import Image
    img = Image.fromarray(arr)
    if fmt == "jpg":
        img.save(path, format="JPEG", quality=int(quality), subsampling=0, optimize=True)
    else:
        img.save(path, format="PNG", compress_level=4, pnginfo=pnginfo)


def _write_workflow_json(path: str, prompt, extra_pnginfo) -> bool:
    wf = extra_pnginfo.get("workflow") if isinstance(extra_pnginfo, dict) else None
    if wf is None and prompt is None:
        return False
    with open(path, "w", encoding="utf-8") as f:
        json.dump(wf if wf is not None else {"prompt": prompt}, f, indent=2,
                  ensure_ascii=False)
    return True


def _find_ffmpeg() -> Optional[str]:
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _probe(path: str) -> dict:
    """width, height, fps, frames, duration, audio via ffprobe (best effort)."""
    info: dict = {}
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        ff = _find_ffmpeg()
        if ff:
            cand = os.path.join(os.path.dirname(ff), "ffprobe.exe" if os.name == "nt" else "ffprobe")
            ffprobe = cand if os.path.isfile(cand) else None
    if not ffprobe:
        return info
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                            "stream=codec_type,width,height,r_frame_rate,nb_frames,"
                            "sample_rate,channels:format=duration", "-of", "json", path],
                           capture_output=True, text=True)
        j = json.loads(r.stdout or "{}")
        for st in j.get("streams", []):
            if st.get("codec_type") == "video" and "width" not in info:
                info["width"], info["height"] = st.get("width"), st.get("height")
                fr = st.get("r_frame_rate", "")
                if "/" in fr:
                    a, b = fr.split("/")
                    info["fps"] = round(float(a) / float(b), 3) if float(b) else None
                nf = st.get("nb_frames")
                info["frames"] = int(nf) if nf and str(nf).isdigit() else None
            elif st.get("codec_type") == "audio" and "audio_sr" not in info:
                info["audio_sr"] = int(st.get("sample_rate") or 0)
                info["audio_ch"] = int(st.get("channels") or 0)
        dur = (j.get("format") or {}).get("duration")
        info["duration"] = round(float(dur), 2) if dur else None
    except Exception:
        pass
    return info


def _extract_last_frame(video_path: str, target: str) -> bool:
    ff = _find_ffmpeg()
    if not ff:
        return False
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-sseof", "-1",
           "-i", video_path, "-update", "1", "-q:v", "2", target]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and os.path.isfile(target)


def _open_in_file_manager(path: str):
    system = platform.system()
    if system == "Windows":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif system == "Darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


def _open_with_default_app(path: str):
    system = platform.system()
    if system == "Windows":
        os.startfile(path)  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _is_temp_download(path: str) -> bool:
    """True if the file lives in the Grok temp download folder (then we MOVE
    instead of copy, so no duplicate is left behind)."""
    try:
        from .grok_imagine_nodes import _download_dir
        return os.path.abspath(path).startswith(os.path.abspath(_download_dir("")))
    except Exception:
        return False


TT_FOLDER = ("Destination folder on any drive, e.g. D:\\Videos\\grok. Created if it "
             "does not exist. This node never writes into the ComfyUI output folder.")
TT_FILENAME = ("Base name for the files. A six-digit counter is always appended: "
               "name_000000, name_000001, ... The counter continues from the highest "
               "number already in the folder, so nothing is ever overwritten. "
               "Placeholders: %date% (2026-09-25), %time% (143012), "
               "%date:yyyy-MM-dd%. Ignored when filename_input is connected.")
TT_FILENAME_INPUT = ("Optional: connect a text (e.g. from a Text node or the 'filename' "
                     "output of a loader) to use it as the base name instead of the "
                     "filename field above. The counter is still appended.")
TT_PROMPT_TEXT = ("Optional: connect the 'prompt' output of the Grok node. It is written "
                  "as name_000000.txt next to the file when save_prompt_txt is on.")


# --------------------------------------------------------------------------- #
# Grok Video Save
# --------------------------------------------------------------------------- #
class GrokVideoSave:
    CATEGORY = "Grok/Imagine"
    FUNCTION = "save"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("saved_path",)
    OUTPUT_TOOLTIPS = ("Full path of the saved MP4, e.g. D:\\Videos\\clip_000003.mp4.",)
    OUTPUT_NODE = True
    DESCRIPTION = ("Moves the Grok MP4 to your folder with a numbered name (no "
                   "re-encoding), plus optional prompt .txt, workflow .json and base "
                   "image .jpg. Preview with sound and buttons in the node.")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_path": ("STRING", {"forceInput": True, "tooltip":
                    "Connect the 'video_path' output of Grok Imagine Video / Video Edit / "
                    "Video Extend. The file is MOVED here from the temp download folder, "
                    "exactly as delivered by xAI - no re-encoding, sound kept."}),
                "folder": ("STRING", {"default": "D:/grok_videos", "tooltip": TT_FOLDER}),
                "filename": ("STRING", {"default": "clip", "tooltip": TT_FILENAME}),
                "save_prompt_txt": ("BOOLEAN", {"default": True, "tooltip":
                    "Write name_000000.txt with the prompt (needs prompt_text connected)."}),
                "save_workflow_json": ("BOOLEAN", {"default": True, "tooltip":
                    "Write name_000000.json with the complete ComfyUI workflow. Load it "
                    "later via Workflow > Open (or drag & drop) to restore every setting."}),
                "save_base_image": ("BOOLEAN", {"default": True, "tooltip":
                    "Write name_000000_base.jpg - the start image you fed into the video "
                    "node (needs base_image connected). JPEG quality 95."}),
            },
            "optional": {
                "filename_input": ("STRING", {"forceInput": True, "tooltip": TT_FILENAME_INPUT}),
                "prompt_text": ("STRING", {"forceInput": True, "tooltip": TT_PROMPT_TEXT}),
                "base_image": ("IMAGE", {"tooltip":
                    "Optional: connect the same image you gave the video node as 'image' "
                    "(image-to-video). Saved as name_000000_base.jpg so video and start "
                    "image stay together. For a batch only the first image is saved."}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO",
                       "unique_id": "UNIQUE_ID"},
        }

    def save(self, video_path, folder, filename, save_prompt_txt=True,
             save_workflow_json=True, save_base_image=True, filename_input=None,
             prompt_text=None, base_image=None, prompt=None, extra_pnginfo=None,
             unique_id=None):
        tag = "[Grok Video Save]"
        src = (video_path or "").strip().strip('"')
        if not os.path.isfile(src):
            raise RuntimeError(f"{tag} video_path not found: {src}")

        out_dir = _resolve_folder(folder, tag)
        raw_name = filename_input if (filename_input or "").strip() else filename
        name = _sanitize(_apply_placeholders(raw_name))
        ext = os.path.splitext(src)[1].lower() or ".mp4"
        base = _next_numbered(out_dir, name, ext)
        dest = os.path.join(out_dir, base + ext)

        moved = _is_temp_download(src)
        if moved:
            shutil.move(src, dest)
        else:
            shutil.copy2(src, dest)
        files = [dest]
        print(f"{tag} {'moved' if moved else 'copied'} -> {dest}")

        if save_prompt_txt and (prompt_text or "").strip():
            p = os.path.join(out_dir, base + ".txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write(str(prompt_text).strip() + "\n")
            files.append(p)

        if save_workflow_json:
            p = os.path.join(out_dir, base + ".json")
            if _write_workflow_json(p, prompt, extra_pnginfo):
                files.append(p)

        if save_base_image and base_image is not None:
            p = os.path.join(out_dir, base + "_base.jpg")
            _save_image_array(_tensor_to_uint8(base_image[0]), p, "jpg", 95)
            files.append(p)

        info = _probe(dest)
        size_mb = os.path.getsize(dest) / (1024 * 1024)
        if info.get("audio_sr"):
            ch = {1: "mono", 2: "stereo"}.get(info.get("audio_ch"), f"{info.get('audio_ch')} ch")
            audio = f"{info['audio_sr']} Hz {ch}"
        else:
            audio = "no audio track"
        res = f"{info.get('width', '?')}x{info.get('height', '?')}"
        status = (f"{os.path.basename(dest)} · {res} · {info.get('duration', '?')} s · "
                  f"{size_mb:.1f} MB · {'moved' if moved else 'copied'}, no re-encode")

        token = register_preview_file(dest)
        _remember(unique_id, {"kind": "video", "dir": out_dir, "base": base,
                              "main": dest, "files": files, "token": token})
        print(f"{tag} {status} | audio: {audio} | companions: "
              f"{', '.join(os.path.basename(f) for f in files[1:]) or 'none'}")
        return {
            "ui": {
                "grok_status": [status],
                "grok_audio": [audio],
                "grok_video": [{"token": token, "node_id": str(unique_id)}],
                "grok_files": [[os.path.basename(f) for f in files]],
            },
            "result": (dest,),
        }


# --------------------------------------------------------------------------- #
# Grok Image Save
# --------------------------------------------------------------------------- #
class GrokImageSave:
    CATEGORY = "Grok/Imagine"
    FUNCTION = "save"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("saved_path",)
    OUTPUT_TOOLTIPS = ("Full path of the first saved image (for a batch the others "
                       "follow with the next numbers).",)
    OUTPUT_NODE = True
    DESCRIPTION = ("Saves IMAGE batches from Generate / Edit to your folder with a "
                   "numbered name, plus optional prompt .txt and workflow .json. "
                   "Preview and buttons in the node. Never touches the ComfyUI output folder.")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {"tooltip":
                    "The image(s) to save - connect the 'images' output of Grok Imagine "
                    "Generate / Edit or any IMAGE. Every image of a batch gets its own "
                    "number."}),
                "folder": ("STRING", {"default": "D:/grok_images", "tooltip": TT_FOLDER}),
                "filename": ("STRING", {"default": "image", "tooltip": TT_FILENAME}),
                "format": (["png", "jpg"], {"default": "png", "tooltip":
                    "png = lossless, larger, keeps the workflow embedded in the file "
                    "(drag & drop into ComfyUI). jpg = smaller, quality 95, no embedded "
                    "workflow (use save_workflow_json)."}),
                "save_prompt_txt": ("BOOLEAN", {"default": True, "tooltip":
                    "Write name_000000.txt with the prompt (needs prompt_text connected). "
                    "For a batch every image gets its own .txt."}),
                "save_workflow_json": ("BOOLEAN", {"default": True, "tooltip":
                    "Write name_000000.json with the complete workflow (once per run)."}),
            },
            "optional": {
                "filename_input": ("STRING", {"forceInput": True, "tooltip": TT_FILENAME_INPUT}),
                "prompt_text": ("STRING", {"forceInput": True, "tooltip": TT_PROMPT_TEXT}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO",
                       "unique_id": "UNIQUE_ID"},
        }

    def save(self, images, folder, filename, format="png", save_prompt_txt=True,
             save_workflow_json=True, filename_input=None, prompt_text=None,
             prompt=None, extra_pnginfo=None, unique_id=None):
        tag = "[Grok Image Save]"
        out_dir = _resolve_folder(folder, tag)
        raw_name = filename_input if (filename_input or "").strip() else filename
        name = _sanitize(_apply_placeholders(raw_name))
        ext = "." + format

        pnginfo = None
        if format == "png":
            from PIL.PngImagePlugin import PngInfo
            pnginfo = PngInfo()
            if prompt is not None:
                pnginfo.add_text("prompt", json.dumps(prompt))
            if extra_pnginfo:
                for k, v in extra_pnginfo.items():
                    pnginfo.add_text(k, v if isinstance(v, str) else json.dumps(v))

        files, tokens, first_base = [], [], None
        n = int(images.shape[0])
        for i in range(n):
            base = _next_numbered(out_dir, name, ext)
            first_base = first_base or base
            p = os.path.join(out_dir, base + ext)
            _save_image_array(_tensor_to_uint8(images[i]), p, format, 95, pnginfo)
            files.append(p)
            tokens.append({"token": register_preview_file(p), "name": os.path.basename(p)})
            if save_prompt_txt and (prompt_text or "").strip():
                t = os.path.join(out_dir, base + ".txt")
                with open(t, "w", encoding="utf-8") as f:
                    f.write(str(prompt_text).strip() + "\n")
                files.append(t)
            if save_workflow_json and i == 0:
                j = os.path.join(out_dir, base + ".json")
                if _write_workflow_json(j, prompt, extra_pnginfo):
                    files.append(j)

        h, w = int(images.shape[1]), int(images.shape[2])
        status = f"{n} image(s) · {w}x{h} · {format} · {os.path.basename(files[0])}"
        _remember(unique_id, {"kind": "image", "dir": out_dir, "base": first_base,
                              "main": files[0], "files": files})
        print(f"{tag} {status} -> {out_dir}")
        return {
            "ui": {
                "grok_status": [status],
                "grok_images": [tokens],
                "grok_files": [[os.path.basename(f) for f in files]],
            },
            "result": (files[0],),
        }


# --------------------------------------------------------------------------- #
# Button actions
# --------------------------------------------------------------------------- #
def handle_action(action: str, node_id: str, payload: dict) -> dict:
    e = _entry(node_id)
    if e is None:
        return {"ok": False, "message": "Nothing saved by this node in the current "
                                        "session yet (run the workflow first)."}
    main = e["main"]

    if action == "reveal":
        if not os.path.isfile(main):
            return {"ok": False, "message": "File no longer exists."}
        _open_in_file_manager(main)
        return {"ok": True, "message": f"Showing {os.path.basename(main)} in the file manager"}

    if action == "open":
        if not os.path.isfile(main):
            return {"ok": False, "message": "File no longer exists."}
        _open_with_default_app(main)
        return {"ok": True, "message": f"Opening {os.path.basename(main)}"}

    if action == "save_last_frame":
        if e.get("kind") != "video":
            return {"ok": False, "message": "Only available for videos."}
        target = os.path.join(e["dir"], e["base"] + "_last.jpg")
        if not _extract_last_frame(main, target):
            return {"ok": False, "message": "ffmpeg could not extract the last frame."}
        with _LOCK:
            if target not in e["files"]:
                e["files"].append(target)
        return {"ok": True, "message": f"Saved {os.path.basename(target)}"}

    if action == "delete":
        deleted = []
        for f in list(e["files"]):
            if os.path.isfile(f):
                try:
                    os.remove(f)
                    deleted.append(os.path.basename(f))
                except Exception as ex:
                    print(f"{LOG} could not delete {f}: {ex}")
        with _LOCK:
            _SESSION.pop(str(node_id), None)
        if not deleted:
            return {"ok": False, "message": "Nothing to delete (files already gone)."}
        return {"ok": True, "message": f"Deleted {len(deleted)} file(s)", "files": deleted}

    return {"ok": False, "message": f"Unknown action '{action}'"}


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def register_routes(version: str):
    try:
        from server import PromptServer
        from aiohttp import web
    except Exception as e:  # pragma: no cover
        print(f"{LOG} routes not registered: {e}")
        return
    srv = PromptServer.instance
    if getattr(srv, "_grok_imagine_routes", False):
        return
    srv._grok_imagine_routes = True

    @srv.routes.post("/grok_imagine/action")
    async def grok_action(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        try:
            result = handle_action(str(data.get("action", "")), str(data.get("node_id", "")), data)
        except Exception as e:
            print(f"{LOG} action failed: {e}")
            result = {"ok": False, "message": str(e)}
        return web.json_response(result)

    @srv.routes.get("/grok_imagine/file")
    async def grok_file(request):
        path = _file_for_token(request.query.get("token", ""))
        if not path or not os.path.isfile(path):
            return web.Response(status=404, text="not found")
        return web.FileResponse(path, headers={"Cache-Control": "no-store"})

    print(f"[Grok Imagine] v{version} routes registered")


NODE_CLASS_MAPPINGS = {
    "GrokVideoSave": GrokVideoSave,
    "GrokImageSave": GrokImageSave,
    "GrokSaveVideo": GrokVideoSave,  # legacy ID from 1.x/2.x - re-add the node once
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "GrokVideoSave": "Grok Video Save 🎬",
    "GrokImageSave": "Grok Image Save 🖼",
    "GrokSaveVideo": "Grok Video Save (legacy ID) 🎬",
}
