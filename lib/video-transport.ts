import { parseDetections, type DetectionPacket } from './detections';
export function connectVideo(stream: MediaStream, endpoint: string, callbacks: { onOpen: () => void; onDetections: (packet: DetectionPacket) => void; onError: (message: string) => void }) {
  const url = new URL(endpoint);
  if (!['ws:', 'wss:'].includes(url.protocol)) throw new Error('Usa una dirección ws:// o wss://.');
  if (location.protocol === 'https:' && url.protocol !== 'wss:') throw new Error('Esta página necesita un endpoint seguro wss://.');
  if (typeof MediaRecorder === 'undefined') throw new Error('Este navegador no permite transmitir video. Prueba Chrome o Edge.');
  const mimeType = ['video/webm;codecs=vp8', 'video/webm;codecs=vp9', 'video/mp4'].find(t => MediaRecorder.isTypeSupported(t));
  if (!mimeType) throw new Error('No hay un formato de video compatible.');
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
      const s = stream.getVideoTracks()[0].getSettings();
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
  socket.onclose = () => fail('Se perdió la conexión con el backend. Vuelve a conectar para continuar.');
  return stop;
}
