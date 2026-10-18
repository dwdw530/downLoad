import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {xhsUrl, isXhsPage} from '../chrome-extension/media.js';

const source = readFileSync(new URL('../chrome-extension/xhs-page.js', import.meta.url), 'utf8');
const sandbox = {globalThis: {}, console, location: {hostname: 'www.xiaohongshu.com'},
  document: {addEventListener() {}}, URL, HTMLVideoElement: class {}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const parse = sandbox.globalThis.__dawXhsParse;
assert.ok(parse?.xhsNoteVideo, 'xhs-page.js must expose __dawXhsParse outside the browser');

const fixture = name => JSON.parse(readFileSync(new URL(`./fixtures/xhs/${name}.json`, import.meta.url), 'utf8'));
const ID = '66c3e1a3000000001e02b5b3';

test('both extension manifests inject the XHS MAIN script on apex and www hosts', () => {
  for (const relative of ['../chrome-extension/manifest.json', '../dist/chrome-extension/manifest.json']) {
    const manifest = JSON.parse(readFileSync(new URL(relative, import.meta.url), 'utf8'));
    const entry = manifest.content_scripts.find(item => item.js?.includes('xhs-page.js'));
    assert.deepEqual(entry?.matches?.slice().sort(), ['*://www.xiaohongshu.com/*', '*://xiaohongshu.com/*']);
  }
  for (const relative of ['../chrome-extension/xhs-page.js', '../chrome-extension/content.js']) {
    const name = relative.split('/').pop();
    assert.equal(readFileSync(new URL(`../dist/chrome-extension/${name}`, import.meta.url), 'utf8'),
      readFileSync(new URL(relative, import.meta.url), 'utf8'),
      `formal dist ${name} must match source`);
  }
});

test('xhsUrl normalizes explore and discovery paths and drops source tokens', () => {
  const url = 'https://www.xiaohongshu.com/explore/66c3e1a3000000001e02b5b3';
  for (const input of [url, url + '?xsec_token=abc&xsec_source=pc_search',
    'https://xiaohongshu.com/explore/66c3e1a3000000001e02b5b3',
    'https://www.xiaohongshu.com/discovery/item/66c3e1a3000000001e02b5b3']) {
    assert.equal(xhsUrl(input), url);
  }
  assert.equal(isXhsPage('https://www.xiaohongshu.com/user/profile/123'), true);
  for (const input of ['https://www.xiaohongshu.com/user/profile/123',
    'https://xhslink.com/a/abc123', url.replace(/b5b3/, 'B5B3'),
    url.replace('www.', 'user:secret@www.'), url.replace('.com', '.com:444'),
    url.replace('xiaohongshu.com', 'xiaohongshu.com.evil.test'),
    'https://www.xiaohongshu.com/explore/66c3e1a3000000001e02b5b3\n', null]) {
    assert.equal(xhsUrl(input), null, String(input));
  }
});

test('xhsNoteVideo reads EF4/EF5 buckets, upgrades http and keeps portrait tiers', () => {
  const pc = parse.xhsNoteVideo(fixture('pc-video'), ID, 1080);
  assert.equal(pc.id, ID);
  assert.equal(pc.duration, 12.34);
  // EF4: 720 master + 1080 master + 1080 backup. EF5 is .m4s and must never be offered.
  assert.equal(pc.formats.length, 3);
  assert.ok(pc.formats.every(format => format.url.startsWith('https://')));
  assert.ok(pc.formats.every(format => new URL(format.url).hostname.endsWith('.xhscdn.com')));
  assert.ok(pc.formats.every(format => new URL(format.url).pathname.endsWith('.mp4')));
  assert.deepEqual(Array.from(pc.formats, format => format.height), [1280, 1920, 1920]);

  const h5 = parse.xhsNoteVideo(fixture('h5-video'), '66c3e1a3000000001e02b5b4', 720);
  assert.equal(h5.duration, 8);
  assert.equal(h5.formats.length, 1);
  assert.equal(h5.formats[0].url, 'https://sns-video-v3.xhscdn.com/stream/79/110/259/720_259.mp4');
});

test('xhsNoteVideo measures tiers on the short edge, not on height', () => {
  assert.equal(parse.xhsNoteVideo(fixture('pc-video'), ID, 480), null);
  assert.equal(parse.xhsNoteVideo(fixture('pc-video'), ID, 720).formats.length, 1);
  assert.equal(parse.xhsNoteVideo(fixture('pc-video'), ID, 1080).formats.length, 3);
  assert.equal(parse.streamTier(1080, 1920), 1080);
  assert.equal(parse.streamTier(1920, 1080), 1080);
});

test('xhsNoteVideo fails closed on unsupported notes, missing duration and bad ids', () => {
  assert.equal(parse.xhsNoteVideo(fixture('image-note'), '66c3e1a3000000001e02b5b5', 720), null);
  assert.equal(parse.xhsNoteVideo(fixture('av1-only'), '66c3e1a3000000001e02b5b7', 720), null);
  assert.equal(parse.xhsNoteVideo(fixture('fragment-only'), '66c3e1a3000000001e02b5b8', 720), null);
  assert.equal(parse.xhsNoteVideo(fixture('non-xhscdn'), '66c3e1a3000000001e02b5b9', 720), null);
  assert.equal(parse.xhsNoteVideo(fixture('note-id-mismatch'), '66c3e1a3000000001e02b5b3', 720), null);
  assert.equal(parse.xhsNoteVideo(fixture('h5-video'), '66c3e1a3000000001e02b5b3', 720), null);
  const stalePc = fixture('pc-video');
  stalePc.note.noteDetailMap[ID].note.noteId = '66c3e1a3000000001e02b5b4';
  assert.equal(parse.xhsNoteVideo(stalePc, ID, 720), null);
  assert.equal(parse.xhsNoteVideo(null, ID, 720), null);
  assert.equal(parse.xhsNoteVideo(fixture('pc-video'), 'zzz', 720), null);
});

test('xhsNoteVideo falls back from capa.duration to stream duration in milliseconds', () => {
  const parsed = parse.xhsNoteVideo(fixture('no-capa-duration'), '66c3e1a3000000001e02b5b6', 720);
  assert.equal(parsed.duration, 9);
  assert.equal(parsed.formats.length, 1);
});

test('xhsNoteVideo enforces the candidate cap after tier filtering', () => {
  const big = {note: {noteDetailMap: {[ID]: {note: {noteId: ID, type: 'video', video: {
    capa: {duration: 3},
    media: {stream: {EF4: Array.from({length: 10}, (_, index) => ({
      masterUrl: `http://sns-video-v3.xhscdn.com/stream/${index}.mp4`, backupUrls: [
        `http://sns-video-v3.xhscdn.com/stream/${index}-a.mp4`,
        `http://sns-video-v3.xhscdn.com/stream/${index}-b.mp4`],
      width: 720, height: 1280, videoCodec: 'EF4', streamType: 259}))}}}}}}}};
  assert.equal(parse.xhsNoteVideo(big, ID, 1080).formats.length, 6);
  const mixed = {note: {noteDetailMap: {[ID]: {note: {noteId: ID, type: 'video', video: {
    capa: {duration: 3},
    media: {stream: {EF4: [
      ...Array.from({length: 6}, (_, index) => ({
        masterUrl: `http://sns-video-v3.xhscdn.com/stream/1440-${index}.mp4`, backupUrls: [],
        width: 1440, height: 2560, videoCodec: 'EF4', streamType: 258
      })),
      {masterUrl: 'http://sns-video-v3.xhscdn.com/stream/720-after-cap.mp4', backupUrls: [],
        width: 720, height: 1280, videoCodec: 'EF4', streamType: 258}
    ]}}}}}}}};
  const capped = parse.xhsNoteVideo(mixed, ID, 720);
  assert.equal(capped.formats.length, 1);
  assert.match(capped.formats[0].url, /720-after-cap/);
});

test('xhsNoteVideo unwraps reactive state and string encoded media fields', () => {
  const ref = value => ({__v_isRef: true, value});
  const item = {masterUrl: 'http://sns-video-v3.xhscdn.com/stream/reactive.mp4',
    backupUrls: JSON.stringify(['http://sns-video-v3.xhscdn.com/stream/reactive-backup.mp4']),
    width: '720', height: '1280', videoCodec: ref('EF4'), streamType: '259'};
  const stream = JSON.stringify({EF4: [item]});
  const note = {noteId: ref(ID), type: ref('video'),
    video: ref({capa: ref({duration: '12.5'}), media: ref({stream})})};
  const state = {state: ref({note: {noteDetailMap: {[ID]: ref({note: ref(note)})}}})};
  const parsed = parse.xhsNoteVideo(state, ID, 720);
  assert.equal(parsed.duration, 12.5);
  assert.equal(parsed.formats.length, 2);
  assert.equal(parsed.formats[0].width, 720);
  assert.equal(parsed.formats[0].url, 'https://sns-video-v3.xhscdn.com/stream/reactive.mp4');
});

test('xhsPlayerVideo accepts only the current signed XHS MP4 source', () => {
  const video = {currentSrc: 'http://sns-video-v3.xhscdn.com/stream/player.mp4?sign=test', duration: 6,
    videoWidth: 1080, videoHeight: 1920};
  const parsed = parse.xhsPlayerVideo(video, ID, 1080);
  assert.equal(parsed.id, ID);
  assert.equal(parsed.formats[0].height, 1920);
  assert.equal(parsed.formats[0].url.startsWith('https://'), true);
  assert.equal(parse.xhsPlayerVideo({...video, currentSrc: 'blob:https://xiaohongshu.com/id'}, ID, 1080), null);
  assert.equal(parse.xhsPlayerVideo({...video, currentSrc: 'https://cdn.example.test/player.mp4'}, ID, 1080), null);
  assert.equal(parse.xhsPlayerVideo({...video, duration: NaN}, ID, 1080), null);
  assert.equal(parse.xhsPlayerVideo({...video, videoWidth: 1440, videoHeight: 2560}, ID, 720), null);
});
