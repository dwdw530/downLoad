import test from 'node:test';
import assert from 'node:assert/strict';
import {classify, classifyResponse, upsert, displayName, publicItem, youtubeUrl} from '../chrome-extension/media.js';

test('classifies MIME and URL while preserving signed query strings', () => {
  assert.equal(classify('https://cdn.test/stream?id=3', 'video/mp4; charset=binary'), 'mp4');
  assert.equal(classify('https://cdn.test/movie.WEBM?token=xyz'), 'webm');
  assert.equal(classify('https://cdn.test/master.m3u8?token=xyz'), 'hls');
  assert.equal(classify('https://cdn.test/manifest', 'application/dash+xml'), 'dash');
});
test('rejects segments, audio, unsafe schemes and embedded credentials', () => {
  for (const url of ['blob:https://cdn.test/id', 'file:///video.mp4', 'https://u:p@cdn.test/video.mp4',
    'https://cdn.test/part.m4s', 'https://cdn.test/part.ts']) assert.equal(classify(url, 'video/mp4'), null);
  assert.equal(classify('https://cdn.test/audio.mp4', 'audio/mp4'), null);
});
test('deduplicates only the same frame and keeps a stable request ID', () => {
  let items = upsert([], {id:'one', url:'https://cdn.test/a.mp4', frameId:0}, 1);
  items = upsert(items, {id:'two', url:'https://cdn.test/a.mp4', frameId:0, size:500}, 2);
  assert.equal(items.length, 1);
  assert.equal(items[0].id, 'one');
  assert.equal(items[0].size, 500);
  items = upsert(items, {id:'three', url:'https://cdn.test/a.mp4', frameId:1}, 3);
  assert.equal(items.length, 2);
});
test('bounds per-page history and expires old entries', () => {
  let items = [];
  for(let i=0;i<80;i++) items=upsert(items,{id:String(i),url:`https://cdn.test/${i}.mp4`,frameId:0},i);
  assert.equal(items.length, 60);
  items=upsert(items,{id:'new',url:'https://cdn.test/new.mp4',frameId:0},1800100);
  assert.equal(items.length, 1);
});
test('labels decode paths without exposing signed query credentials', () => {
  assert.equal(displayName('https://cdn.test/a%20b.mp4?secret=abc','mp4'),'a b.mp4');
  assert.equal(displayName('https://cdn.test/%zz','mp4'),'video.mp4');
  assert.equal(publicItem({id:1,headers:{Cookie:'secret'}}).headers,undefined);
});

test('XHR video is visible as a stream, not discarded or offered as a whole file', () => {
  const url='https://cdn.test/videoplayback?id=abc&range=0-999';
  assert.equal(classifyResponse(url,'video/mp4','xmlhttprequest'),'stream');
  assert.equal(classifyResponse(url,'video/mp4','media'),'stream');
  assert.equal(classifyResponse('https://cdn.test/movie.mp4','video/mp4','media'),'mp4');
  assert.equal(classifyResponse('https://cdn.test/movie.webm','video/webm','media'),'webm');
});
test('POST multiplexed media, audio and segments remain explicitly unsupported', () => {
  assert.equal(classifyResponse('https://cdn.test/videoplayback','application/vnd.yt-ump','xmlhttprequest','POST'),'stream');
  assert.equal(classifyResponse('https://cdn.test/track','audio/mp4','xmlhttprequest'),'stream');
  assert.equal(classifyResponse('https://cdn.test/part.m4s','video/mp4','media'),'stream');
  assert.equal(classifyResponse('https://cdn.test/part.ts','video/mp2t','media'),'stream');
  assert.equal(classifyResponse('file:///part.mp4','video/mp4','xmlhttprequest'),null);
  assert.equal(classifyResponse('https://cdn.test/login.mp4','text/html','media'),null);
});
test('stream evidence is bounded per track without altering actionable signed links', () => {
  const old={id:'one',url:'https://cdn.test/videoplayback?itag=137&range=0-9',kind:'stream',mime:'video/mp4',frameId:0};
  const newer={...old,id:'two',url:'https://cdn.test/videoplayback?itag=137&range=10-19'};
  let items=upsert(upsert([],old,1),newer,2);
  assert.equal(items.length,1);
  assert.equal(items[0].id,'one');
  items=upsert(items,{...newer,id:'three',url:'https://cdn.test/videoplayback?itag=140&range=0-9'},3);
  assert.equal(items.length,2);
  const direct={id:'direct',url:'https://cdn.test/movie.mp4?signature=abc',kind:'mp4',frameId:0};
  assert.equal(upsert([],direct,1)[0].url,direct.url);
});
test('a later DOM observation cannot turn an unsupported stream into a download', () => {
  const item={id:'one',url:'https://cdn.test/part.mp4',kind:'stream',frameId:0};
  const updated=upsert(upsert([],item,1),{...item,id:'two',kind:'mp4'},2)[0];
  assert.equal(updated.kind,'stream');
  assert.equal(publicItem(updated).unsupportedReason,'流式媒体暂不支持下载');
});

test('YouTube page URLs normalize to one video without timestamps or playlists', () => {
  const expected = 'https://www.youtube.com/watch?v=ayl6TcSsre8';
  for (const url of [expected + '&t=1s&list=abc', 'https://youtu.be/ayl6TcSsre8',
    'https://m.youtube.com/shorts/ayl6TcSsre8', 'https://www.youtube-nocookie.com/embed/ayl6TcSsre8']) {
    assert.equal(youtubeUrl(url), expected);
  }
  for (const url of ['https://youtube.com.evil.test/watch?v=ayl6TcSsre8',
    'https://user:secret@youtube.com/watch?v=ayl6TcSsre8', 'file:///ayl6TcSsre8',
    'https://youtube.com/playlist?list=abc', 'https://youtube.com/watch?v=bad']) {
    assert.equal(youtubeUrl(url), null);
  }
});
