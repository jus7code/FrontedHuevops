import test from 'node:test';
import assert from 'node:assert/strict';
import { createHistoryTracker, captureEgg } from '../lib/detection-history.ts';
const egg = { id: 'egg-1', label: 'Huevo', status: 'Sano', confidence: .95, box: [.1, .2, .3, .4] };
const photo = d => `data:image/jpeg;base64,${d?.status ?? 'test'}`;
function feed(tracker, time, status = 'Sano', id = String(time)) {
  return tracker.collect([{ ...egg, status, id }], 50, time, photo);
}
function finish(tracker) {
  for (const time of [0, 500, 1000, 1500, 2000, 2500]) assert.deepEqual(feed(tracker, time), []);
  return tracker.tick(3000);
}
test('waits three seconds and saves majority with matching photo despite changing IDs', () => {
  const tracker = createHistoryTracker();
  for (const [time, status] of [[0, 'Sano'], [500, 'Roto'], [1000, 'Roto'], [1500, 'Sano'], [2000, 'Roto'], [2500, 'Roto']]) {
    assert.deepEqual(feed(tracker, time, status), []);
  }
  assert.equal(tracker.snapshot(2000).remaining, 1);
  assert.deepEqual(tracker.tick(2999), []);
  const [entry] = tracker.tick(3000);
  assert.equal(entry.status, 'Roto');
  assert.equal(entry.photo, photo({ status: 'Roto' }));
  assert.deepEqual(entry.votes, { Sano: 2, Roto: 4 });
  assert.equal(tracker.snapshot(3000).phase, 'remove');
  assert.deepEqual(tracker.tick(3100), []);
  assert.deepEqual(feed(tracker, 4000), []);
});
test('requires consecutive empty responses before next egg, including across reconnects', () => {
  const tracker = createHistoryTracker();
  assert.equal(finish(tracker).length, 1);
  tracker.pause();
  assert.deepEqual(feed(tracker, 4000), []);
  tracker.collect([], 50, 4100, photo);
  feed(tracker, 4500);
  tracker.collect([], 50, 4600, photo);
  tracker.collect([], 50, 5100, photo);
  assert.equal(tracker.snapshot(5100).phase, 'remove');
  tracker.collect([], 50, 5600, photo);
  assert.equal(tracker.snapshot(5600).phase, 'ready');
  feed(tracker, 5700);
  assert.equal(tracker.snapshot(5700).phase, 'collecting');
});
test('silence cannot count as removal and stale predictions cannot finalize', () => {
  const tracker = createHistoryTracker();
  feed(tracker, 0);
  assert.deepEqual(tracker.tick(3000), []);
  assert.equal(tracker.snapshot(3000).phase, 'ready');
  const locked = createHistoryTracker(); finish(locked);
  locked.collect([], 50, 3500, photo);
  locked.collect([], 50, 6000, photo);
  assert.equal(locked.snapshot(6000).phase, 'remove');
});
test('tie requests another inspection without saving', () => {
  const tracker = createHistoryTracker();
  [0, 500, 1000, 1500, 2000, 2500].forEach((time, i) => feed(tracker, time, i % 2 ? 'Roto' : 'Sano'));
  assert.deepEqual(tracker.tick(3000), []);
  assert.equal(tracker.snapshot(3000).phase, 'remove');
  assert.match(tracker.snapshot(3000).message, /inconcluso/);
});
test('multiple eggs, low confidence, removal and stopping cancel incomplete votes', () => {
  const tracker = createHistoryTracker();
  feed(tracker, 0);
  tracker.collect([egg, { ...egg, id: 'other' }], 50, 1000, photo);
  assert.equal(tracker.snapshot(1000).phase, 'ready');
  tracker.collect([egg], 99, 1500, photo);
  assert.equal(tracker.snapshot(1500).votes.Sano, 0);
  feed(tracker, 2000);
  tracker.collect([], 50, 2500, photo);
  assert.equal(tracker.snapshot(2500).phase, 'ready');
  feed(tracker, 3000); tracker.pause();
  assert.deepEqual(tracker.tick(6000), []);
});
test('missing photos cannot save and predictions after deadline do not alter votes', () => {
  const tracker = createHistoryTracker();
  for (const time of [0, 500, 1000, 1500, 2000, 2500]) tracker.collect([egg], 50, time, () => null);
  assert.deepEqual(tracker.tick(3000), []);
  assert.match(tracker.snapshot(3000).message, /foto/);
  const ready = createHistoryTracker();
  for (const time of [0, 500, 1000, 1500, 2000, 2500]) feed(ready, time);
  const [entry] = feed(ready, 3000, 'Roto');
  assert.deepEqual(entry.votes, { Sano: 6, Roto: 0 });
});
test('crops normalized boxes against native video dimensions', () => {
  let args;
  const canvas = { getContext: () => ({ drawImage: (...values) => { args = values; } }), toDataURL: photo };
  const original = globalThis.document;
  globalThis.document = { createElement: () => canvas };
  try {
    const video = { readyState: 2, videoWidth: 1280, videoHeight: 720 };
    assert.equal(captureEgg(video, egg), photo('image/jpeg'));
    assert.deepEqual(args.slice(1, 5), [128, 144, 384, 288]);
    assert.equal(canvas.width, 320);
    assert.equal(canvas.height, 240);
    assert.equal(captureEgg({ ...video, readyState: 0 }, egg), null);
  } finally { globalThis.document = original; }
});
