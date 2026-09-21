import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

// Compile the actual adapter in memory; browser APIs are controlled test doubles.
const source = readFileSync(new URL('../lib/video-transport.ts', import.meta.url), 'utf8')
  .replace("'./detections'", JSON.stringify(new URL('../lib/detections.ts', import.meta.url).href));
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { connectVideo } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));

function setup(t, supported = ['video/webm;codecs=vp8']) {
  const sockets = [], recorders = [], errors = [], results = [];
  class Socket {
    static OPEN = 1;
    readyState = 0; bufferedAmount = 0; sent = [];
    constructor(url) { this.url = String(url); sockets.push(this); }
    send(value) { this.sent.push(value); }
    open() { this.readyState = 1; this.onopen(); }
    close() { this.readyState = 3; this.onclose?.({ code: 1000, reason: '' }); }
    message(value) { this.onmessage({ data: JSON.stringify(value) }); }
  }
  class Recorder {
    static isTypeSupported(mime) { return supported.includes(mime); }
    state = 'inactive';
    constructor(stream, options) { this.options = options; recorders.push(this); }
    start(ms) { assert.equal(sockets.at(-1).sent.length, 1); this.interval = ms; this.state = 'recording'; }
    stop() { this.state = 'inactive'; }
    chunk(data) { this.ondataavailable({ data }); }
  }
  for (const [key, value] of Object.entries({ WebSocket: Socket, MediaRecorder: Recorder, location: { protocol: 'http:' } })) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
    t.after(() => previous ? Object.defineProperty(globalThis, key, previous) : delete globalThis[key]);
  }
  const stream = { getVideoTracks: () => [{ readyState: 'live', getSettings: () => ({ width: 1280, height: 720, frameRate: 30 }) }] };
  const callbacks = { onOpen() {}, onError: e => errors.push(e), onDetections: d => results.push(d) };
  const connect = () => { const stop = connectVideo(stream, 'ws://localhost:8000/video', callbacks); t.after(stop); return stop; };
  return { sockets, recorders, errors, results, stream, callbacks, connect };
}

test('sends start before ordered binary chunks, receives detections, cleans up', t => {
  const f = setup(t); const stop = f.connect(); const socket = f.sockets[0]; socket.open();
  const start = JSON.parse(socket.sent[0]);
  assert.deepEqual({ ...start, sessionId: 'uuid' }, { type: 'start', sessionId: 'uuid', mimeType: 'video/webm;codecs=vp8', width: 1280, height: 720, fps: 30, timesliceMs: 200 });
  assert.match(start.sessionId, /^[a-f0-9-]{36}$/);
  const recorder = f.recorders[0]; assert.equal(recorder.interval, 200);
  const chunks = [new Blob(['header']), new Blob(['continuation'])];
  chunks.forEach(data => recorder.chunk(data)); assert.deepEqual(socket.sent.slice(1), chunks);
  const packet = { type: 'detections', sessionId: start.sessionId, sequence: 1, detections: [{ id: 'egg-1', label: 'Huevo', status: 'Roto', confidence: .97, box: [.1,.2,.3,.4] }] };
  socket.message(packet); socket.message(packet);
  socket.message({ ...packet, sessionId: 'other', sequence: 2 });
  socket.message({ ...packet, sequence: 2, detections: [] });
  assert.equal(f.results.length, 2); assert.deepEqual(f.results[1].detections, []);
  stop(); recorder.chunk(new Blob(['late']));
  assert.equal(socket.sent.length, 3); assert.equal(recorder.state, 'inactive'); assert.equal(socket.readyState, 3);
  assert.deepEqual(f.errors, []);
});
test('rejects MP4-only browsers and missing dimensions before connecting', t => {
  const f = setup(t, ['video/mp4']);
  assert.throws(f.connect, /WebM/); assert.equal(f.sockets.length, 0);
});
test('requires actual positive integer video dimensions', t => {
  const f = setup(t);
  for (const settings of [{}, { width: 0, height: 720 }, { width: 1280.5, height: 720 }]) {
    const stream = { getVideoTracks: () => [{ readyState: 'live', getSettings: () => settings }] };
    assert.throws(() => connectVideo(stream, 'ws://localhost:8000/video', f.callbacks), /dimensiones/);
  }
  assert.equal(f.sockets.length, 0);
});
test('supports VP9 fallback and ends overloaded streams without dropping chunks', t => {
  const f = setup(t, ['video/webm;codecs=vp9']); f.connect(); const socket = f.sockets[0]; socket.open();
  assert.equal(JSON.parse(socket.sent[0]).mimeType, 'video/webm;codecs=vp9');
  socket.bufferedAmount = 4000000; f.recorders[0].chunk(new Blob(['overflow']));
  assert.equal(socket.readyState, 3); assert.equal(socket.sent.length, 1); assert.equal(f.errors.length, 1);
});
test('reports server close reason and uses a new session on reconnect', t => {
  const f = setup(t); f.connect(); f.sockets[0].open();
  f.sockets[0].onclose({ code: 1003, reason: 'Unsupported codec' });
  assert.match(f.errors[0], /1003.*Unsupported codec/);
  assert.equal(f.recorders[0].state, 'inactive');
  f.connect(); f.sockets[1].open();
  assert.notEqual(JSON.parse(f.sockets[0].sent[0]).sessionId, JSON.parse(f.sockets[1].sent[0]).sessionId);
});
