import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

// Locks the kind whitelists: download() in background.js must accept every kind that
// content.js / popup.js render as clickable, otherwise the button works but the send fails.
const read = name => readFileSync(new URL(`../chrome-extension/${name}`, import.meta.url), 'utf8');
const kindsFrom = line => JSON.parse(line.slice(line.indexOf('['), line.lastIndexOf(']') + 1));

test('download() whitelist covers every clickable kind', () => {
  const background = read('background.js');
  const downloadKinds = kindsFrom(background.split('\n')
    .find(line => line.includes('includes(item.kind)') && line.includes('未找到完整视频地址')));
  const contentKinds = kindsFrom(read('content.js').split('\n')
    .find(line => line.includes('button.disabled =')));
  const popupKinds = kindsFrom(read('popup.js').split('\n')
    .find(line => line.includes('const supported =')));
  for (const kind of ['mp4', 'webm', 'youtube', 'x', 'bilibili', 'douyin', 'xhs', 'hls', 'dash']) {
    assert.ok(downloadKinds.includes(kind), `background download() must accept ${kind}`);
    assert.ok(contentKinds.includes(kind), `content.js must render ${kind} as clickable`);
    assert.ok(popupKinds.includes(kind), `popup.js must render ${kind} as clickable`);
  }
});
