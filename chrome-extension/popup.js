let tabId;
const $ = id => document.getElementById(id);
const send = message => chrome.runtime.sendMessage({...message, tabId});
function node(tag, value, className) {
  const element = document.createElement(tag);
  element.textContent = value;
  element.className = className || "";
  return element;
}
async function refresh() {
  const result = await send({action: "list"});
  $("enabled").checked = result.enabled !== false;
  $("quality").value = String(result.videoHeight || 720);
  $("items").replaceChildren();
  if (!result.enabled || !result.items?.length) {
    $("items").append(node("div", result.enabled === false ? "视频识别已关闭" : result.emptyMessage || "当前页面不可用", "empty"));
    return;
  }
  for (const item of result.items) {
    const row = node("div", "", "row");
    const info = node("div", "");
    info.append(node("div", item.name, "name"));
    const supported = !item.protected && ["mp4", "webm", "youtube", "x", "bilibili", "douyin", "xhs", "hls", "dash"].includes(item.kind);
    const note = item.protected ? "受保护视频" : ["youtube", "x", "bilibili", "douyin", "xhs", "hls", "dash"].includes(item.kind) ? `最高 ${result.videoHeight || 720}p · MP4 ${["x", "hls", "dash"].includes(item.kind) ? "视频" : "音视频"}`
      : supported ? item.sizeLabel : item.unsupportedReason;
    info.append(node("div", `${item.kindLabel} · ${note}`, "meta"));
    const button = node("button", "下载", "download");
    button.disabled = !supported;
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.textContent = "发送中";
      $("status").className = "";
      $("status").textContent = "正在连接daw下载器…";
      const response = await send({action: "download", id: item.id});
      $("status").textContent = response.ok ? `已加入下载：${response.filename}` : response.error || "发送失败";
      $("status").className = response.ok ? "" : "error";
      button.textContent = response.ok ? "已发送" : "重试";
      button.disabled = !!response.ok;
    });
    row.append(info, button);
    $("items").append(row);
  }
}
$("enabled").addEventListener("change", async () => {
  await send({action: "setting", enabled: $("enabled").checked});
  await refresh();
});
$("refresh").addEventListener("click", refresh);
$("quality").addEventListener("change", async () => {
  await send({action: "setting", videoHeight: Number($("quality").value)});
  await refresh();
});
(async () => {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  tabId = tab?.id;
  await refresh();
  const connection = await send({action: "ping"});
  $("connection").textContent = connection.ok ? "daw下载器已连接" : connection.error;
})().catch(() => { $("status").textContent = "当前页面不可用"; $("status").className = "error"; });

// Independent link-download feedback: local storage only, so clearing session storage for
// the video switch never removes it, and it never reuses the video status element.
const LINK_STATUS_KEY = "fileDownloadStatus";
function renderLinkStatus(entry) {
  const target = $("linkStatus");
  if (!target) return;
  if (!entry) {
    target.textContent = "右键网页链接可发送到 daw 下载器";
    target.className = "";
    return;
  }
  const pending = entry.code === "sending";
  const detail = pending ? "正在发送到 daw 下载器…"
    : entry.ok ? `已加入下载队列${entry.filename ? `：${entry.filename}` : ""}` : entry.message || "发送失败";
  const time = entry.time ? ` · ${new Date(entry.time).toLocaleTimeString()}` : "";
  target.textContent = detail + time;
  target.className = entry.ok || pending ? "" : "error";
}
chrome.storage.local.get({[LINK_STATUS_KEY]: null})
  .then(result => renderLinkStatus(result[LINK_STATUS_KEY]))
  .catch(() => renderLinkStatus(null));
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes[LINK_STATUS_KEY]) renderLinkStatus(changes[LINK_STATUS_KEY].newValue);
});
