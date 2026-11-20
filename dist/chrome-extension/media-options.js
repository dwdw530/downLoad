export const VIDEO_HEIGHTS = [0, 480, 720, 1080, 1440, 2160];
export const DEFAULT_MEDIA_OPTIONS = {
  mode: "video", audio_format: "best", profile: "compatible", subtitles: "none"
};
const allowed = {
  mode: ["video", "audio"], audio_format: ["best", "mp3"],
  profile: ["compatible", "source"], subtitles: ["none", "zh", "en", "zh,en", "all"]
};

export function normalizeMediaOptions(value = {}) {
  const options = {...DEFAULT_MEDIA_OPTIONS};
  for (const [key, values] of Object.entries(allowed)) {
    if (values.includes(value?.[key])) options[key] = value[key];
  }
  return options;
}

export async function readMediaSettings(api = chrome) {
  const saved = await api.storage.local.get({
    videoHeight: 720, mediaOptions: DEFAULT_MEDIA_OPTIONS
  });
  return {
    height: VIDEO_HEIGHTS.includes(saved.videoHeight) ? saved.videoHeight : 720,
    options: normalizeMediaOptions(saved.mediaOptions)
  };
}

export function needsMediaFeature(height, options) {
  return ![480, 720, 1080].includes(height) || Object.keys(options)
    .some(key => options[key] !== DEFAULT_MEDIA_OPTIONS[key]);
}

export async function mediaRequestId(id, height, options) {
  const source = JSON.stringify([id, height,
    ...Object.keys(DEFAULT_MEDIA_OPTIONS).map(key => options[key])]);
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(source));
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, "0")).join("");
}

export function describeMediaSettings(height, options) {
  const output = options.mode === "audio"
    ? (options.audio_format === "mp3" ? "仅音频 · MP3" : "仅音频 · 保留源格式")
    : `${height ? `最高 ${height}p` : "最高可用画质"} · ${options.profile === "source" ? "MKV 源画质优先" : "MP4 兼容模式"}`;
  return output + (options.subtitles === "none" ? "" : " · 下载已有字幕");
}
