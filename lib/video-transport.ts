import { parseDetections, type DetectionPacket } from './detections';
export function connectVideo(stream: MediaStream, endpoint: string, callbacks: { onOpen: () => void; onDetections: (packet: DetectionPacket) => void; onError: (message: string) => void }) {
  const url = new URL(endpoint);
  if (!['ws:', 'wss:'].includes(url.protocol)) throw new Error('Usa una dirección ws:// o wss://.');
  if (location.protocol === 'https:' && url.protocol !== 'wss:') throw new Error('Esta página necesita un endpoint seguro wss://.');
  if (typeof MediaRecorder === 'undefined') throw new Error('Este navegador no permite transmitir video. Prueba Chrome o Edge.');
  const mimeType = ['video/webm;codecs=vp8', 'video/webm;codecs=vp9'].find(t => MediaRecorder.isTypeSupported(t));
  if (!mimeType) throw new Error('El backend requiere WebM con VP8 o VP9. Usa un navegador compatible, como Chrome o Edge.');
  const track = stream.getVideoTracks()[0];
  if (!track || track.readyState !== 'live') throw new Error('Activa una cámara antes de transmitir.');
  const s = track.getSettings();
  if (!Number.isInteger(s.width) || !Number.isInteger(s.height) || (s.width ?? 0) <= 0 || (s.height ?? 0) <= 0) {
    throw new Error('No se pudieron obtener las dimensiones reales de la cámara. Vuelve a activarla.');
  }
  const socket = new WebSocket(url);
  const sessionId = crypto.randomUUID();
  let recorder: MediaRecorder | undefined;
  let closed = false;
  let lastSequence = -1;
  const timeout = setTimeout(() => fail('El servidor no respondió. Revisa el endpoint.'), 10000);
  function stop() {
    if (closed) return;
    closed = true; clearTimeout(timeout);
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    socket.close();
  }
  function fail(message: string) { if (!closed) { stop(); callbacks.onError(message); } }
  socket.onopen = () => {
    if (closed) return;
    clearTimeout(timeout);
    try {
      socket.send(JSON.stringify({ type: 'start', sessionId, mimeType, width: s.width, height: s.height, fps: s.frameRate, timesliceMs: 200 }));
      recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 2000000 });
      recorder.ondataavailable = ({ data }) => {
        if (closed || !data.size || socket.readyState !== WebSocket.OPEN) return;
        // Never discard dependent video chunks. End overloaded sessions instead.
        if (socket.bufferedAmount + data.size > 4000000) return fail('La conexión es demasiado lenta. Se detuvo el envío; puedes volver a conectar.');
        socket.send(data);
      };
      recorder.onerror = () => fail('El navegador interrumpió la codificación del video.');
      recorder.start(200); callbacks.onOpen();
    } catch { fail('No se pudo iniciar la transmisión de video.'); }
  };
  socket.onmessage = ({ data }) => {
    if (closed) return;
    try {
      if (typeof data !== 'string' || data.length > 1000000) throw new Error();
      const packet = parseDetections(data);
      if (packet.sessionId !== sessionId || packet.sequence <= lastSequence) return;
      lastSequence = packet.sequence; callbacks.onDetections(packet);
    } catch { fail('El backend respondió con un formato incompatible. Revisa el contrato de integración.'); }
  };
  socket.onerror = () => fail('No se pudo conectar al backend. Revisa su disponibilidad.');
  socket.onclose = event => fail(`El backend cerró la conexión (código ${event.code})${event.reason ? `: ${event.reason}` : '. Vuelve a conectar para continuar.'}`);
  return stop;
}
