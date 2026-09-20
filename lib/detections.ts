export type Detection = { id: string; label: string; confidence: number; box: [number, number, number, number] };
export type DetectionPacket = { type: 'detections'; sessionId: string; sequence: number; detections: Detection[] };
export function parseDetections(raw: string): DetectionPacket {
  const v = JSON.parse(raw);
  if (v?.type !== 'detections' || typeof v.sessionId !== 'string' || !Number.isSafeInteger(v.sequence) || v.sequence < 0 || !Array.isArray(v.detections) || v.detections.length > 500) throw new Error('Respuesta incompatible');
  const ids = new Set<string>();
  for (const d of v.detections) {
    if (!d || typeof d.id !== 'string' || ids.has(d.id) || typeof d.label !== 'string' || !Number.isFinite(d.confidence) || d.confidence < 0 || d.confidence > 1 || !Array.isArray(d.box) || d.box.length !== 4 || !d.box.every((n: unknown) => typeof n === 'number' && Number.isFinite(n) && n >= 0 && n <= 1) || d.box[2] <= 0 || d.box[3] <= 0 || d.box[0] + d.box[2] > 1.00001 || d.box[1] + d.box[3] > 1.00001) throw new Error('Detección inválida');
    ids.add(d.id);
  }
  return v;
}
