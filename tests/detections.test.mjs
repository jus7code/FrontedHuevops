import test from 'node:test';
import assert from 'node:assert/strict';
import { parseDetections } from '../lib/detections.ts';
const sample = () => ({ type: 'detections', sessionId: 'session', sequence: 0, detections: [{ id: '1', label: 'Huevo', status: 'Sano', confidence: .95, box: [.1, .2, .3, .4] }] });
test('accepts normalized detections and empty results', () => {
  assert.deepEqual(parseDetections(JSON.stringify(sample())), sample());
  assert.equal(parseDetections(JSON.stringify({ ...sample(), detections: [] })).detections.length, 0);
});
test('rejects malformed packets and invalid coordinates', () => {
  for (const raw of ['bad json', '{}', 'null']) assert.throws(() => parseDetections(raw));
  for (const box of [[.9, 0, .5, .5], [-1, 0, .1, .1], [0, 0, 0, .2], [0, 0, .1], [0, 0, '1', .1]]) {
    const packet = sample(); packet.detections[0].box = box;
    assert.throws(() => parseDetections(JSON.stringify(packet)));
  }
});
test('rejects invalid confidence, duplicate IDs and invalid sequence', () => {
  const packet = sample(); packet.detections[0].confidence = 95;
  assert.throws(() => parseDetections(JSON.stringify(packet)));
  const duplicate = sample(); duplicate.detections.push(duplicate.detections[0]);
  assert.throws(() => parseDetections(JSON.stringify(duplicate)));
  assert.throws(() => parseDetections(JSON.stringify({ ...sample(), sequence: -1 })));
});
test('requires the exact health status values', () => {
  for (const status of ['Roto', 'Sano']) {
    const packet = sample(); packet.detections[0].status = status;
    assert.equal(parseDetections(JSON.stringify(packet)).detections[0].status, status);
  }
  for (const status of [undefined, 'sano', 'roto', 'Desconocido']) {
    const packet = sample();
    if (status === undefined) delete packet.detections[0].status;
    else packet.detections[0].status = status;
    assert.throws(() => parseDetections(JSON.stringify(packet)));
  }
});
