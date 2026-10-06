// Grok Imagine - inline previews, status lines and buttons.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const STATUS_NODES = new Set([
    "GrokImagineGenerate", "GrokImagineEdit",
    "GrokImagineVideo", "GrokImagineVideoEdit", "GrokImagineVideoExtend",
    "GrokVideoSave", "GrokImageSave", "GrokSaveVideo",
]);
const VIDEO_NODES = new Set(["GrokImagineVideo", "GrokImagineVideoEdit", "GrokImagineVideoExtend",
                             "GrokVideoSave", "GrokSaveVideo"]);
const SAVE_NODES = new Set(["GrokVideoSave", "GrokImageSave", "GrokSaveVideo"]);

function toast(severity, summary, detail) {
    const t = app.extensionManager?.toast;
    if (t?.add) t.add({ severity, summary, detail, life: severity === "error" ? 6000 : 3500 });
    else if (severity === "error") alert(`${summary}\n${detail ?? ""}`);
    else console.log(`[Grok Imagine] ${summary} ${detail ?? ""}`);
}

async function callAction(node, action) {
    try {
        const res = await api.fetchApi("/grok_imagine/action", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action, node_id: String(node.id) }),
        });
        const data = await res.json();
        toast(data.ok ? "success" : "warn", "Grok Imagine", data.message);
        return data;
    } catch (e) {
        toast("error", "Grok Imagine", String(e));
        return { ok: false };
    }
}

function fileUrl(token) {
    return api.apiURL(`/grok_imagine/file?token=${encodeURIComponent(token)}&t=${Date.now()}`);
}

function buildPanel(withVideo, withImages) {
    const wrap = document.createElement("div");
    Object.assign(wrap.style, {
        display: "flex", flexDirection: "column", gap: "4px", width: "100%", height: "100%",
        boxSizing: "border-box", padding: "4px 6px", overflow: "hidden",
        fontSize: "11px", fontFamily: "sans-serif", color: "#ccc",
    });
    const status = document.createElement("div");
    status.textContent = "Not run yet in this session.";
    Object.assign(status.style, { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" });
    const sub = document.createElement("div");
    sub.style.fontWeight = "bold";
    wrap.append(status, sub);

    let video = null, images = null;
    if (withVideo) {
        video = document.createElement("video");
        video.controls = true; video.muted = false; video.preload = "metadata";
        Object.assign(video.style, { width: "100%", flex: "1 1 auto", minHeight: "0",
            background: "#111", borderRadius: "4px", display: "none", objectFit: "contain" });
        wrap.append(video);
    }
    if (withImages) {
        images = document.createElement("div");
        Object.assign(images.style, { display: "flex", gap: "4px", flexWrap: "wrap",
            overflow: "auto", flex: "1 1 auto", minHeight: "0" });
        wrap.append(images);
    }
    return { wrap, status, sub, video, images };
}

app.registerExtension({
    name: "Comfy.GrokImagine.Preview",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!STATUS_NODES.has(nodeData.name)) return;
        const isVideo = VIDEO_NODES.has(nodeData.name);
        const isSave = SAVE_NODES.has(nodeData.name);
        const isImageSave = nodeData.name === "GrokImageSave";

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const ret = onNodeCreated?.apply(this, arguments);
            const node = this;
            const opts = { serialize: false };

            if (isSave) {
                node.addWidget("button", "📂 Reveal in Explorer", null, () => callAction(node, "reveal"), opts);
                node.addWidget("button", isImageSave ? "🖼 Open image" : "▶ Open video (with sound)", null,
                    () => callAction(node, "open"), opts);
                if (!isImageSave) {
                    node.addWidget("button", "🖼 Save Last Frame", null, () => callAction(node, "save_last_frame"), opts);
                }
                node.addWidget("button", "🗑 Delete this set", null, async () => {
                    const files = node._grokFiles?.join("\n  ") ?? "the last saved files";
                    if (!confirm(`Delete?\n\n  ${files}\n\nThis cannot be undone.`)) return;
                    const res = await callAction(node, "delete");
                    if (res.ok) {
                        node._grokFiles = null;
                        node._grokUI.status.textContent = "Deleted.";
                        node._grokUI.sub.textContent = "";
                        if (node._grokUI.video) { node._grokUI.video.style.display = "none"; node._grokUI.video.removeAttribute("src"); node._grokUI.video.load(); }
                        if (node._grokUI.images) node._grokUI.images.innerHTML = "";
                    }
                }, opts);
            }

            const ui = buildPanel(isVideo, isImageSave);
            node._grokUI = ui;
            node.addDOMWidget("grok_status", "GROK_STATUS", ui.wrap, {
                serialize: false, hideOnZoom: false,
                getMinHeight: () => (isVideo || isImageSave ? 60 : 24),
            });
            const extra = isVideo ? 200 : (isImageSave ? 160 : 30);
            node.setSize([Math.max(node.size[0], 380), node.computeSize()[1] + extra]);
            return ret;
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            const ui = this._grokUI;
            if (!ui) return;
            const status = message?.grok_status?.[0];
            if (status) { ui.status.textContent = status; ui.status.title = status; }
            const audio = message?.grok_audio?.[0];
            if (audio !== undefined) {
                const ok = !/no audio/i.test(audio);
                ui.sub.textContent = ok ? `✓ Audio: ${audio}` : `⚠ ${audio}`;
                ui.sub.style.color = ok ? "#7fd67f" : "#f0b04a";
            } else if (status && /cost_usd=([0-9.]+)/.test(status)) {
                ui.sub.textContent = `Billed: $${status.match(/cost_usd=([0-9.]+)/)[1]}`;
                ui.sub.style.color = "#9ecbff";
            }
            if (message?.grok_files?.[0]) this._grokFiles = message.grok_files[0];

            const v = message?.grok_video?.[0];
            if (v && ui.video) {
                ui.video.src = fileUrl(v.token);
                ui.video.style.display = "block";
                ui.video.load();
            }
            const imgs = message?.grok_images?.[0];
            if (imgs && ui.images) {
                ui.images.innerHTML = "";
                for (const it of imgs) {
                    const img = document.createElement("img");
                    img.src = fileUrl(it.token);
                    img.title = it.name;
                    Object.assign(img.style, { maxHeight: "140px", maxWidth: "100%", borderRadius: "4px", objectFit: "contain" });
                    ui.images.append(img);
                }
            }
            this.setDirtyCanvas(true, true);
        };
    },
});
