import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {DEFAULT_MEDIA_OPTIONS, mediaRequestId, readMediaSettings,
  needsMediaFeature, describeMediaSettings} from '../chrome-extension/media-options.js';
if (!globalThis.crypto) globalThis.crypto = webcrypto;

test('video, audio, format and subtitle requests never share a receipt ID', async () => {
  const base = DEFAULT_MEDIA_OPTIONS;
  const choices = [base, {...base, mode:'audio'}, {...base, mode:'audio', audio_format:'mp3'},
    {...base, profile:'source'}, {...base, subtitles:'en'}];
  const ids = await Promise.all(choices.map(options => mediaRequestId('resource-123', 2160, options)));
  assert.equal(new Set(ids).size, choices.length);
  assert.equal(await mediaRequestId('resource-123', 2160, base), ids[0]);
  assert.notEqual(await mediaRequestId('resource-123', 1440, base), ids[0]);
  for (const id of ids) assert.match(id, /^[a-f0-9]{64}$/);
});

test('highest source setting survives storage and never displays 720p', async () => {
  const api = {storage:{local:{get:async () => ({videoHeight:0,
    mediaOptions:{profile:'source', subtitles:'zh,en'}})}}};
  const {height, options} = await readMediaSettings(api);
  assert.equal(height, 0);
  assert.match(describeMediaSettings(height, options), /最高可用画质/);
  assert.equal(needsMediaFeature(height, options), true);
  assert.equal(needsMediaFeature(720, DEFAULT_MEDIA_OPTIONS), false);
});
