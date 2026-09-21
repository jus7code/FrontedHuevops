import type { Detection } from './detections';

export const HISTORY_LIMIT = 100;
export const COLLECTION_MS = 3000;
const STALE_MS = 1500;
const REMOVAL_MS = 1000;
export type HistoryEntry = Detection & { key: string; photo: string; capturedAt: number; votes: { Sano: number; Roto: number } };
export type Inspection = { phase: 'ready' | 'collecting' | 'remove'; remaining: number; votes: { Sano: number; Roto: number }; message: string };

export function createHistoryTracker() {
  let phase: Inspection['phase'] = 'ready';
  let started = 0;
  let lastValid = 0;
  let lastPacket: number | null = null;
  let emptySince: number | null = null;
  let votes = { Sano: 0, Roto: 0 };
  let best: Partial<Record<Detection['status'], { detection: Detection; photo: string }>> = {};
  let message = 'Coloca un solo huevo frente a la c\u00e1mara.';
  function ready(text = 'Coloca el siguiente huevo frente a la c\u00e1mara.') {
    phase = 'ready'; votes = { Sano: 0, Roto: 0 }; best = {}; emptySince = null; message = text;
  }
  function tick(now: number): HistoryEntry[] {
    if (phase !== 'collecting') return [];
    if (now - lastValid >= STALE_MS) {
      ready('Se interrumpi\u00f3 la detecci\u00f3n. Coloca el huevo y repite la lectura.');
      return [];
    }
    if (now - started < COLLECTION_MS) return [];
    phase = 'remove'; emptySince = null;
    if (votes.Sano === votes.Roto || votes.Sano + votes.Roto < 2) {
      message = 'Resultado inconcluso. Retira el huevo y vuelve a colocarlo para repetir.';
      return [];
    }
    const status = votes.Roto > votes.Sano ? 'Roto' : 'Sano';
    const sample = best[status];
    if (!sample) {
      message = 'No se pudo guardar la foto. Retira el huevo y repite la lectura.';
      return [];
    }
    message = `Clasificaci\u00f3n final: ${status} \u00b7 ${status === 'Roto' ? 'Desechar' : 'Mantener'}. Ya puedes retirar el huevo.`;
    return [{ ...sample.detection, key: crypto.randomUUID(), photo: sample.photo, capturedAt: now, votes: { ...votes } }];
  }
  return {
    snapshot(now: number): Inspection {
      return { phase, remaining: phase === 'collecting' ? Math.max(0, Math.ceil((COLLECTION_MS - (now - started)) / 1000)) : 0, votes: { ...votes }, message };
    },
    pause() {
      if (phase === 'collecting') ready('Lectura interrumpida. Coloca el huevo para repetir.');
      emptySince = null; lastPacket = null;
    },
    tick,
    collect(detections: Detection[], threshold: number, now: number, capture: (d: Detection) => string | null): HistoryEntry[] {
      const gap = lastPacket === null || now - lastPacket >= STALE_MS;
      lastPacket = now;
      if (phase === 'remove') {
        // Only consecutive, explicit empty backend responses confirm removal.
        if (detections.length) emptySince = null;
        else {
          if (gap || emptySince === null) emptySince = now;
          if (now - emptySince >= REMOVAL_MS) ready();
        }
        return [];
      }
      if (phase === 'collecting' && now - lastValid >= STALE_MS) ready();
      if (detections.length !== 1 || detections[0].confidence * 100 < threshold) {
        ready(detections.length > 1 ? 'Deja un solo huevo para iniciar la lectura.' : 'Coloca el huevo y mantenlo visible con suficiente confianza.');
        return [];
      }
      // Finalize before counting a prediction received after the window.
      if (phase === 'collecting' && now - started >= COLLECTION_MS) return tick(now);
      const detection = detections[0];
      if (phase === 'ready') {
        phase = 'collecting'; started = now;
        message = 'Mant\u00e9n el huevo quieto. Estamos reuniendo predicciones.';
      }
      lastValid = now;
      votes[detection.status]++;
      const previous = best[detection.status];
      if (!previous || detection.confidence > previous.detection.confidence) {
        const photo = capture(detection);
        if (photo) best[detection.status] = { detection: { ...detection }, photo };
      }
      return [];
    },
  };
}

export function captureEgg(video: HTMLVideoElement, detection: Detection): string | null {
  if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) return null;
  const [x, y, w, h] = detection.box;
  const width = Math.min(w * video.videoWidth, video.videoWidth * (1 - x));
  const height = Math.min(h * video.videoHeight, video.videoHeight * (1 - y));
  if (width <= 0 || height <= 0) return null;
  const scale = Math.min(1, 320 / Math.max(width, height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  const context = canvas.getContext('2d');
  if (!context) return null;
  try {
    context.drawImage(video, x * video.videoWidth, y * video.videoHeight, width, height, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', .85);
  } catch { return null; }
}
