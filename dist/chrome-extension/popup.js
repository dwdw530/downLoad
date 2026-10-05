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
    const supported = !item.protected && ["mp4", "webm", "youtube"].includes(item.kind);
    const note = item.protected ? "受保护视频" : item.kind === "youtube" ? `最高 ${result.videoHeight || 720}p · MP4 音视频`
      : supported ? item.sizeLabel : item.unsupportedReason;
    info.append(node("div", `${item.kindLabel} · ${note}`, "meta"));
    const button = node("button", "下载", "download");
    button.disabled = !supported;
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.textContent = "发送中";
      $("status").className = "";
      $("status").textContent = "正在连接老王下载器…";
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
  $("connection").textContent = connection.ok ? "老王下载器已连接" : connection.error;
})().catch(() => { $("status").textContent = "当前页面不可用"; $("status").className = "error"; });
