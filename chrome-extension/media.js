export function youtubeUrl(value) {
  let url;
  try { url = new URL(value); } catch { return null; }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
      (url.port && !["80", "443"].includes(url.port))) return null;
  let id = "";
  if (["youtube.com", "www.youtube.com", "m.youtube.com", "www.youtube-nocookie.com"].includes(url.hostname)) {
    if (url.pathname === "/watch") id = url.searchParams.get("v") || "";
    else if (/^\/(shorts|embed)\//.test(url.pathname)) id = url.pathname.split("/")[2];
  } else if (url.hostname === "youtu.be") id = url.pathname.slice(1);
  return /^[A-Za-z0-9_-]{11}$/.test(id) ? `https://www.youtube.com/watch?v=${id}` : null;
}

export function isXPage(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password &&
      (!url.port || ["80", "443"].includes(url.port)) &&
      ["x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"].includes(url.hostname);
  } catch { return false; }
}

export function xUrl(value) {
  if (typeof value !== "string" || value.length > 2048 || !isXPage(value)) return null;
  const match = /^\/([A-Za-z0-9_]{1,15})\/status\/([0-9]{1,25})(?:\/video\/([1-4]))?\/?$/.exec(new URL(value).pathname);
  return match ? `https://x.com/${match[1]}/status/${match[2]}/video/${match[3] || "1"}` : null;
}

export function bilibiliUrl(value) {
  if (typeof value !== "string" || value.length > 2048 || /[\x00-\x1f]/.test(value)) return null;
  let url;
  try { url = new URL(value); } catch { return null; }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
      (url.port && !["80", "443"].includes(url.port)) ||
      !["bilibili.com", "www.bilibili.com", "m.bilibili.com"].includes(url.hostname)) return null;
  const match = /^\/video\/(BV[A-Za-z0-9]{10}|av[1-9][0-9]{0,19})\/?$/.exec(url.pathname);
  const parts = url.searchParams.has("p") ? url.searchParams.getAll("p") : ["1"];
  if (!match || parts.length !== 1 || !/^[1-9][0-9]{0,4}$/.test(parts[0])) return null;
  return `https://www.bilibili.com/video/${match[1]}/?p=${parts[0]}`;
}

export function classify(url, mime = "") {
  let parsed;
  try { parsed = new URL(url); } catch { return null; }
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) return null;
  const path = parsed.pathname.toLowerCase();
  mime = mime.split(";")[0].trim().toLowerCase();
  // Segments must never be offered as a complete movie.
  if (/\.(m4s|ts|aac|m4a)$/.test(path) || mime.startsWith("audio/")) return null;
  if (path.endsWith(".m3u8") || ["application/vnd.apple.mpegurl", "application/x-mpegurl"].includes(mime)) return "hls";
  if (path.endsWith(".mpd") || mime === "application/dash+xml") return "dash";
  if (mime === "video/mp4" || path.endsWith(".mp4")) return "mp4";
  if (mime === "video/webm" || path.endsWith(".webm")) return "webm";
  return null;
}

export function displayName(url, kind) {
  try {
    const name = decodeURIComponent(new URL(url).pathname.split("/").pop());
    return (name || `video.${kind}`).slice(0, 160);
  } catch { return `video.${kind}`; }
}

export function classifyResponse(url, mime, requestType, method = "GET") {
  let parsed;
  try { parsed = new URL(url); } catch { return null; }
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) return null;
  const type = mime.split(";")[0].trim().toLowerCase();
  const kind = classify(url, mime);
  if (["hls", "dash"].includes(kind)) return kind;
  if (type === "text/html" || type === "application/json") return null;
  if (kind) return requestType === "media" && method === "GET" && !parsed.searchParams.has("range") ? kind : "stream";
  if (/^(video|audio)\//.test(type) || type === "application/vnd.yt-ump") return "stream";
  return null;
}

function streamIdentity(item) {
  const url = new URL(item.url);
  return `${url.origin}${url.pathname}|${item.mime || ""}|${url.searchParams.get("itag") || ""}`;
}

export function upsert(items, candidate, now = Date.now()) {
  const fresh = items.filter(item => now - item.seen < 30 * 60 * 1000);
  const old = fresh.find(item => item.frameId === candidate.frameId && (item.url === candidate.url ||
    (item.kind === "stream" && candidate.kind === "stream" && streamIdentity(item) === streamIdentity(candidate))));
  if (old) Object.assign(old, candidate, {id: old.id, seen: now, size: candidate.size || old.size || 0,
    kind: old.kind === "stream" ? "stream" : candidate.kind});
  else fresh.push({...candidate, seen: now});
  return fresh.slice(-60);
}

export function publicItem(item) {
  const sizeLabel = !item.size ? "大小待确认" : item.size < 1048576
    ? `${Math.max(1, Math.round(item.size / 1024))} KB` : `${(item.size / 1048576).toFixed(1)} MB`;
  return {id: item.id, url: item.url, name: item.name, kind: item.kind, size: item.size,
    sizeLabel, frameId: item.frameId, protected: !!item.protected,
    kindLabel: item.kind === "bilibili" ? "B站" : item.kind === "stream" ? "媒体流" : String(item.kind || "").toUpperCase(),
    unsupportedReason: item.kind === "stream" ? "流式媒体暂不支持下载" : "分段视频暂不支持"};
}
