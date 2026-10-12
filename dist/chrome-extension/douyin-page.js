// Runs only on Douyin, in the page's MAIN world. Never patches fetch/XHR or sends downloads.
(() => {
  if (!["douyin.com", "www.douyin.com"].includes(location.hostname)) return;
  const requestEvent = "daw-douyin-read";
  const responseEvent = "daw-douyin-data";

  function readPlayer(video) {
    if (!(video instanceof HTMLVideoElement) || !video.isConnected) return null;
    const rect = video.getBoundingClientRect();
    if (!rect.width || !rect.height || rect.bottom <= 0 || rect.right <= 0 ||
        rect.top >= innerHeight || rect.left >= innerWidth || getComputedStyle(video).visibility === "hidden") return null;
    const player = window.player;
    if (!player) return null;
    // The player must own this exact DOM element. Never choose the first/last video in a feed.
    const elements = new Set([player.video, player.controls?.video, player._video, player._core?._media]
      .filter(element => element instanceof HTMLVideoElement && element.isConnected));
    if (elements.size !== 1 || !elements.has(video)) return null;
    const plugins = player._config?._config?.pluginsConfig;
    const infos = [player.config?.awemeInfo, player.controls?.playerConfig?.awemeInfo,
      ...["WatchLaterPlugin", "DanmakuPlugin", "PlayClaritySettingPlugin", "Pip"].map(key => plugins?.[key]?.awemeInfo)]
      .filter(Boolean);
    const ids = new Set(infos.map(info => info.awemeId ?? info.aweme_id));
    const id = ids.values().next().value;
    if (ids.size !== 1 || typeof id !== "string" || !/^[1-9][0-9]{0,24}$/.test(id)) return null;
    const info = infos[0];
    const media = info.video;
    if (!media || info.images?.length || info.is_live || info.isLive) return null;
    // HTMLMediaElement.duration is seconds. Metadata may use milliseconds or seconds;
    // it only verifies this binding, and is never used to select another video.
    const duration = video.duration;
    if (!Number.isFinite(duration) || duration <= 0) return null;
    for (const candidate of infos) {
      const reported = candidate.video?.duration;
      if (reported !== undefined && (!Number.isFinite(reported) || reported <= 0 ||
          Math.min(Math.abs(reported - duration), Math.abs(reported / 1000 - duration)) > Math.max(0.5, duration * 0.02))) return null;
    }
    const variants = Array.isArray(media.bit_rate) ? media.bit_rate : Array.isArray(media.bitRateList) ? media.bitRateList : [];
    const fallback = media.playApiH265 ? [media, {playApi: media.playApiH265, isH265: true, width: media.width, height: media.height}] : [media];
    const bitRate = [...variants.slice(0, 40 - fallback.length), ...fallback].map(variant => {
      const address = variant.play_addr || (!Array.isArray(variant.playAddr) ? variant.playAddr : null);
      const urls = address?.url_list || address?.urlList ||
        (Array.isArray(variant.playAddr) ? variant.playAddr.slice(0, 4).map(item => item?.src) : []);
      return {format: variant.format ?? "mp4", is_h265: variant.is_h265 === 1 || variant.isH265 === 1 || variant.isH265 === true,
        play_addr: {width: address?.width ?? variant.width ?? media.width, height: address?.height ?? variant.height ?? media.height,
          url_list: [variant.playApi, ...(Array.isArray(urls) ? urls.slice(0, 4) : [])].filter(value => typeof value === "string").slice(0, 4)}};
    });
    return {aweme_id: id, video: {duration: duration * 1000, bit_rate: bitRate}};
  }

  document.addEventListener(requestEvent, event => {
    if (typeof event.detail !== "string" || !/^[a-f0-9-]{36}$/.test(event.detail)) return;
    let detail = null;
    try { detail = readPlayer(event.target); } catch { /* The player can be replaced while a feed loads. */ }
    event.target.dispatchEvent(new CustomEvent(responseEvent, {detail: JSON.stringify({nonce: event.detail, detail})}));
  }, true);
})();
