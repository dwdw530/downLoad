// Runs only on xiaohongshu.com/www.xiaohongshu.com, in the page's MAIN world. Never patches fetch/XHR,
// never sends network requests, and never attaches globals in a browser environment.
(() => {
  if (location.hostname !== "www.xiaohongshu.com" && location.hostname !== "xiaohongshu.com") return;
  const requestEvent = "daw-xhs-read";
  const responseEvent = "daw-xhs-data";
  const MAX_CANDIDATES = 6;
  const MAX_BACKUPS = 3;

  function unwrap(value) {
    let current = value;
    const seen = new Set();
    for (let depth = 0; depth < 8; depth++) {
      if (!current || typeof current !== "object" || seen.has(current)) return current;
      seen.add(current);
      const isRef = current.__v_isRef === true || current.__isRef === true;
      if (isRef && current.value !== undefined) { current = current.value; continue; }
      if (Object.prototype.hasOwnProperty.call(current, "_value") && current._value !== undefined) {
        current = current._value;
        continue;
      }
      if (Object.keys(current).length === 1 && Object.prototype.hasOwnProperty.call(current, "value")) {
        current = current.value;
        continue;
      }
      return current;
    }
    return current;
  }

  function jsonValue(value) {
    const unwrapped = unwrap(value);
    if (typeof unwrapped !== "string") return unwrapped;
    try { return JSON.parse(unwrapped); } catch { return null; }
  }

  function positiveNumber(value) {
    const number = typeof value === "number" ? value : Number(value);
    return Number.isFinite(number) && number > 0 ? number : null;
  }

  function positiveInteger(value) {
    const number = positiveNumber(value);
    return Number.isInteger(number) ? number : null;
  }

  function noteFor(state, noteId) {
    // Nuxt SSR state: PC stores notes under note.noteDetailMap, H5 under noteData.data.noteData.
    const root = unwrap(state);
    const states = [root, unwrap(root?.state), unwrap(root?.data)].filter(Boolean);
    for (const candidate of states) {
      const map = unwrap(candidate?.note?.noteDetailMap);
      const pc = unwrap(unwrap(map?.[noteId])?.note);
      if (unwrap(pc?.noteId) === noteId) return pc;
      const h5 = unwrap(unwrap(candidate?.noteData)?.data)?.noteData;
      if (unwrap(h5?.noteId) === noteId) return unwrap(h5);
    }
    return null;
  }

  function noteDuration(note) {
    const capa = positiveNumber(unwrap(unwrap(note?.video)?.capa)?.duration);
    if (capa) return capa;
    const streams = jsonValue(unwrap(unwrap(note?.video)?.media)?.stream);
    if (streams && typeof streams === "object") {
      for (const rawGroup of Object.values(streams)) {
        const group = jsonValue(rawGroup);
        if (!Array.isArray(group)) continue;
        for (const rawItem of group) {
          const duration = positiveNumber(unwrap(rawItem)?.duration);
          // Stream metadata is milliseconds today; keep second-scale values as-is.
          if (duration) return duration > 1000 ? duration / 1000 : duration;
        }
      }
    }
    return null;
  }

  // The player requests http:// media from an https page; the same signed CDN path answers over https.
  function secureUrl(value) {
    if (typeof value !== "string" || value.length > 16384 || value.length <= 8) return null;
    return value.startsWith("http://") ? "https://" + value.slice(7) : value;
  }

  function safeFormat(value, width, height, vcodec) {
    try {
      const url = secureUrl(value);
      if (!url) return null;
      const parsed = new URL(url);
      const safeWidth = positiveInteger(width);
      const safeHeight = positiveInteger(height);
      if (parsed.protocol !== "https:" || parsed.username || parsed.password ||
          (parsed.port && parsed.port !== "443") || !parsed.hostname.endsWith(".xhscdn.com") ||
          !parsed.pathname.toLowerCase().endsWith(".mp4") ||
          ![safeWidth, safeHeight].every(size => Number.isInteger(size) && size <= 16384)) return null;
      return {url, width: safeWidth, height: safeHeight, vcodec};
    } catch { return null; }
  }

  function streamCodec(value) {
    // Buckets are labelled EF4/EF5/EF7 today, so the bucket name says nothing about the codec.
    const codec = String(unwrap(value) ?? "").toLowerCase();
    if (!codec || ["av1", "av01", "h266", "vvc"].includes(codec)) return null;
    return codec.includes("265") || codec.includes("hevc") ? "h265" : "h264";
  }

  function streamTier(width, height) {
    // Tiers are measured on the short edge: portrait 1080x1920 is 1080p, not 1920p.
    return Math.min(width, height);
  }

  /** Pure parse of the page state; exported for unit tests only via __dawXhsParse. */
  function xhsNoteVideo(state, noteId, maxHeight) {
    if (typeof noteId !== "string" || !/^[0-9a-f]{24}$/.test(noteId)) return null;
    const note = noteFor(state, noteId);
    const video = unwrap(note?.video);
    if (!note || unwrap(note.type) !== "video" || !video || typeof video !== "object") return null;
    const duration = noteDuration(note);
    if (!duration) return null;
    const stream = jsonValue(unwrap(unwrap(video.media)?.stream));
    if (!stream || typeof stream !== "object") return null;
    const formats = [];
    // Read every bucket by position, never by name: h264/h265 became EF4/EF5 with the same shape.
    for (const rawGroup of Object.values(stream)) {
      const group = jsonValue(rawGroup);
      if (!Array.isArray(group)) continue;
      for (const rawItem of group) {
        if (formats.length >= MAX_CANDIDATES) break;
        const item = unwrap(rawItem);
        const vcodec = streamCodec(item?.videoCodec);
        if (!vcodec) continue;
        const width = positiveInteger(item?.width);
        const height = positiveInteger(item?.height);
        if (!width || !height || streamTier(width, height) > maxHeight) continue;
        const backups = jsonValue(item?.backupUrls);
        const urls = [item?.masterUrl, ...(Array.isArray(backups) ? backups.slice(0, MAX_BACKUPS) : [])];
        for (const rawUrl of urls) {
          if (formats.length >= MAX_CANDIDATES) break;
          const format = safeFormat(unwrap(rawUrl), width, height, vcodec);
          if (format) formats.push(format);
        }
      }
    }
    return formats.length ? {id: noteId, duration, formats} : null;
  }

  function xhsPlayerVideo(video, noteId, maxHeight) {
    if (typeof noteId !== "string" || !/^[0-9a-f]{24}$/.test(noteId) || !video || typeof video !== "object") return null;
    const source = typeof video.currentSrc === "string" && video.currentSrc ? video.currentSrc
      : typeof video.src === "string" ? video.src : "";
    const duration = positiveNumber(video.duration);
    const width = positiveInteger(video.videoWidth || video.width);
    const height = positiveInteger(video.videoHeight || video.height);
    const format = safeFormat(source, width, height, "h264");
    return duration && format && streamTier(format.width, format.height) <= maxHeight
      ? {id: noteId, duration, formats: [format]} : null;
  }

  function readState(video) {
    if (!(video instanceof HTMLVideoElement) || !video.isConnected) return null;
    const rect = video.getBoundingClientRect();
    if (!rect.width || !rect.height || rect.bottom <= 0 || rect.right <= 0 ||
        rect.top >= innerHeight || rect.left >= innerWidth || getComputedStyle(video).visibility === "hidden") return null;
    const match = /^\/(?:explore|discovery\/item)\/([0-9a-f]{24})\/?$/.exec(location.pathname);
    const noteId = match?.[1] || "";
    const parsed = xhsNoteVideo(window.__INITIAL_STATE__, noteId, 1080) || xhsPlayerVideo(video, noteId, 1080);
    if (!parsed) return null;
    // The visible player must agree with the state entry: same duration when metadata is loaded.
    const duration = video.duration;
    if (Number.isFinite(duration) && duration > 0 && parsed.formats.length > 1 &&
        Math.abs(duration - parsed.duration) > Math.max(1, parsed.duration * 0.02)) return null;
    return parsed;
  }

  document.addEventListener(requestEvent, event => {
    if (typeof event.detail !== "string" || !/^[a-f0-9-]{36}$/.test(event.detail)) return;
    let detail = null;
    try { detail = readState(event.target); } catch { /* Page state can be replaced while SPA navigates. */ }
    event.target.dispatchEvent(new CustomEvent(responseEvent, {detail: JSON.stringify({nonce: event.detail, detail})}));
  }, true);

  if (typeof chrome === "undefined") globalThis.__dawXhsParse = {xhsNoteVideo, xhsPlayerVideo, noteFor,
    noteDuration, safeFormat, secureUrl, streamCodec, streamTier, unwrap};
})();
