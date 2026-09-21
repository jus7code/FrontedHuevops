'use client';
import { useEffect, useRef, useState } from 'react';
import { connectVideo } from '../lib/video-transport';
import { BACKEND_VIDEO_URL } from '../lib/backend-config';
import type { Detection } from '../lib/detections';

const demoBoxes: Detection[] = [
  { id: '01', label: 'Huevo', status: 'Sano', confidence: .98, box: [.15, .23, .17, .43] },
  { id: '02', label: 'Huevo', status: 'Roto', confidence: .96, box: [.41, .30, .17, .43] },
  { id: '03', label: 'Huevo', status: 'Sano', confidence: .93, box: [.67, .22, .17, .43] },
];
export default function Home() {
  const video = useRef<HTMLVideoElement>(null);
  const media = useRef<MediaStream | null>(null);
  const disconnect = useRef<(() => void) | null>(null);
  const generation = useRef(0);
  const staleTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [camera, setCamera] = useState(false);
  const [busy, setBusy] = useState(false);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [device, setDevice] = useState('');
  const [demo, setDemo] = useState(false);
  const [endpoint, setEndpoint] = useState(BACKEND_VIDEO_URL);
  const [status, setStatus] = useState<'idle' | 'connecting' | 'live'>('idle');
  const [error, setError] = useState('');
  const [detections, setDetections] = useState<Detection[]>([]);
  const [threshold, setThreshold] = useState(50);
  const [boxes, setBoxes] = useState(true);
  const [ratio, setRatio] = useState(16 / 9);
  const [resolution, setResolution] = useState('—');
  const [fps, setFps] = useState('—');
  const [seconds, setSeconds] = useState(0);
  const [received, setReceived] = useState(false);
  function stopTransmission() {
    disconnect.current?.(); disconnect.current = null;
    clearTimeout(staleTimer.current);
    setStatus('idle'); setDetections([]); setReceived(false);
  }
  function stopCamera() {
    generation.current++; stopTransmission();
    media.current?.getTracks().forEach(t => { t.onended = null; t.stop(); });
    media.current = null;
    if (video.current) video.current.srcObject = null;
    setCamera(false); setBusy(false); setResolution('—'); setFps('—');
  }
  useEffect(() => () => {
    generation.current++; clearTimeout(staleTimer.current); disconnect.current?.();
    media.current?.getTracks().forEach(t => { t.onended = null; t.stop(); });
  }, []);
  useEffect(() => {
    if (!camera && !demo) { setSeconds(0); return; }
    const timer = setInterval(() => setSeconds(s => s + 1), 1000);
    return () => clearInterval(timer);
  }, [camera, demo]);
  useEffect(() => {
    if (!demo) return;
    setDetections(demoBoxes);
    const timer = setInterval(() => setDetections(demoBoxes.map((d, i) => ({ ...d, confidence: d.confidence - Math.random() * .025, box: [d.box[0], d.box[1] + Math.sin(Date.now() / 1800 + i) * .012, d.box[2], d.box[3]] }))), 200);
    return () => clearInterval(timer);
  }, [demo]);
  async function startCamera() {
    stopCamera(); setDemo(false); setError(''); setBusy(true);
    const request = ++generation.current;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('La cámara necesita HTTPS o localhost y un navegador compatible.');
      const stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 }, ...(device ? { deviceId: { exact: device } } : {}) } });
      if (request !== generation.current) { stream.getTracks().forEach(t => t.stop()); return; }
      media.current = stream;
      stream.getVideoTracks()[0].onended = () => { stopCamera(); setError('La cámara se desconectó o dejó de estar disponible.'); };
      if (video.current) { video.current.srcObject = stream; await video.current.play(); }
      if (request !== generation.current) return;
      const settings = stream.getVideoTracks()[0].getSettings();
      setRatio((settings.width || 1280) / (settings.height || 720));
      setResolution(`${settings.width ?? '?'} × ${settings.height ?? '?'}`);
      setFps(settings.frameRate ? String(Math.round(settings.frameRate)) : '—');
      setCamera(true);
      try { setDevices((await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'videoinput')); } catch { /* enumeration is optional */ }
    } catch (e) {
      if (request !== generation.current) return;
      stopCamera();
      const name = e instanceof Error ? e.name : '';
      setError(name === 'NotAllowedError' ? 'Permite el acceso a la cámara desde tu navegador para continuar.' : name === 'NotFoundError' ? 'No encontramos una cámara. Conecta una e intenta de nuevo.' : name === 'NotReadableError' ? 'La cámara está ocupada. Cierra otras aplicaciones que la estén usando.' : e instanceof Error ? e.message : 'No se pudo iniciar la cámara.');
    } finally { if (request === generation.current) setBusy(false); }
  }
  function startTransmission() {
    if (!media.current) return;
    setError(''); setStatus('connecting');
    try {
      disconnect.current = connectVideo(media.current, endpoint.trim(), {
        onOpen: () => setStatus('live'),
        onDetections: packet => {
          setDetections(packet.detections); setReceived(true); clearTimeout(staleTimer.current);
          staleTimer.current = setTimeout(() => { setDetections([]); setReceived(false); }, 1500);
        },
        onError: message => { stopTransmission(); setError(message); },
      });
    } catch (e) { stopTransmission(); setError(e instanceof Error ? e.message : 'Revisa la dirección del backend.'); }
  }
  const visible = detections.filter(d => d.confidence * 100 >= threshold);
  const mean = visible.length ? Math.round(visible.reduce((n, d) => n + d.confidence, 0) / visible.length * 100) : null;
  const elapsed = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  return <div className="shell">
    <aside className="rail"><a className="brand" href="/" aria-label="HuevOps inicio"><span className="egg-mark" /></a><div className="rail-line"/><a className="rail-item active" href="#monitor" aria-label="Monitor">▣</a><a className="rail-item" href="#connection" aria-label="Conexión">⇄</a><a className="rail-item rail-bottom" href="#guide" aria-label="Guía de uso">?</a></aside>
    <div className="workspace"><header><a href="/" className="wordmark">huev<span>ops</span><small>VISION WORKSPACE</small></a><span className="header-note"><i className="dot"/> Sistema de visión en tiempo real</span><span className="avatar">HO</span></header>
    <main id="monitor"><div className="heading"><div><div className="eyebrow">OBSERVA. DETECTA. CUENTA.</div><h1>Cada huevo, a la vista<span>.</span></h1><p>Tu cámara y la inteligencia de tu modelo, en un solo lugar.</p></div><span className={`status-pill ${status === 'live' ? 'green' : ''}`}><i className="dot"/>{demo ? 'Modo demostración' : status === 'live' ? 'Transmitiendo' : status === 'connecting' ? 'Conectando…' : 'Backend sin conectar'}</span></div>
    {error && <div role="alert" className="error">{error}<button onClick={() => setError('')} aria-label="Cerrar aviso">×</button></div>}
    <div className="main-grid"><section className="monitor-card"><div className="card-heading"><h2><span className="small-square"/> Monitor en vivo</h2><span className="mono muted">CAM 01 <span className="divider">/</span> {demo ? 'SIMULACIÓN' : camera ? 'CÁMARA LOCAL' : 'EN ESPERA'}</span></div>
      <div className="viewport"><div className="video-stage" style={{ aspectRatio: demo ? 16 / 9 : ratio }}>
        <video ref={video} autoPlay muted playsInline className={camera ? 'camera visible' : 'camera'} aria-label="Video en vivo de la cámara"/>
        {demo && <div className="demo-scene"><div className="demo-grid"/>{demoBoxes.map((d, i) => <div key={d.id} className={`sample-egg egg-${i}`} style={{ left: `${(d.box[0] + .018) * 100}%`, top: `${(d.box[1] + .025) * 100}%` }}/>)}</div>}
        {!camera && !demo && <div className="empty-camera"><div className="viewfinder"><span className="empty-egg"/></div><h3>Todo empieza con una mirada</h3><p>Activa tu cámara para observar los huevos<br/>y recibir detecciones en tiempo real.</p><button className="primary" disabled={busy} onClick={startCamera}>{busy ? 'Solicitando cámara…' : '◉  Activar cámara'}</button><button className="text-button" disabled={busy} onClick={() => { stopCamera(); setError(''); setDemo(true); }}>Explorar demostración <span>↗</span></button></div>}
        {(demo || camera) && <><div className="video-badge"><i className="dot"/>{demo ? 'DEMO · DATOS SIMULADOS' : status === 'live' ? 'VIDEO ENVIÁNDOSE' : 'VISTA LOCAL'}</div>{boxes && visible.map(d => <div className="detection-box" key={d.id} style={{ left: `${d.box[0] * 100}%`, top: `${d.box[1] * 100}%`, width: `${d.box[2] * 100}%`, height: `${d.box[3] * 100}%` }}><span>{d.label} · {d.status} · {Math.round(d.confidence * 100)}%</span></div>)}<div className="video-time mono">{elapsed}</div></>}
      </div></div><div className="monitor-toolbar"><span><i className={`dot ${camera || demo ? 'on' : ''}`}/>{demo ? 'Vista de ejemplo' : camera ? 'Cámara activa' : 'Cámara desactivada'}</span><div><label className="toggle-label"><input type="checkbox" checked={boxes} onChange={e => setBoxes(e.target.checked)}/> Mostrar cajas</label><button className="icon-button" disabled={!camera && !demo && !busy} onClick={() => { stopCamera(); setDemo(false); }} aria-label="Detener cámara y transmisión">■ <span>Detener</span></button></div></div>
      <div className="metrics"><div><span>HUEVOS EN PANTALLA</span><strong>{demo || received ? String(visible.length).padStart(2, '0') : '—'}<small>detectados</small></strong></div><div><span>CONFIANZA PROMEDIO</span><strong>{mean ?? '—'}<small>{mean !== null ? '%' : 'sin datos'}</small></strong></div><div><span>CAPTURA DE CÁMARA</span><strong>{demo ? '—' : fps}<small>fps</small></strong></div><div><span>TIEMPO DE SESIÓN</span><strong className="time-metric">{elapsed}</strong></div></div>
    </section>
    <aside className="side-panel"><section className="settings-card" id="connection"><div className="card-heading"><h2>Tu estación de trabajo</h2><span>⚙</span></div><div className="settings-body"><div className="section-label"><span>01</span> Fuente de video</div><label htmlFor="camera-select">Cámara</label><select id="camera-select" value={device} disabled={camera || busy} onChange={e => setDevice(e.target.value)}><option value="">Cámara predeterminada</option>{devices.map((d, i) => <option key={d.deviceId} value={d.deviceId}>{d.label || `Cámara ${i + 1}`}</option>)}</select><div className="specs"><span>Resolución <b>{resolution}</b></span><span>Audio <b>Desactivado</b></span></div>{!camera && <button className="secondary full" disabled={busy} onClick={startCamera}>{busy ? 'Esperando permiso…' : 'Activar cámara'}</button>}
    <div className="section-label separated"><span>02</span> Conexión al modelo</div><label htmlFor="endpoint">Endpoint del backend</label><input id="endpoint" type="url" placeholder={BACKEND_VIDEO_URL} value={endpoint} disabled={status !== 'idle'} onChange={e => setEndpoint(e.target.value)} spellCheck={false}/><p className="field-hint">Video continuo WebM. Activa la cámara e inicia la transmisión para recibir las detecciones.</p><button className={status === 'idle' ? 'primary full' : 'secondary full'} disabled={status === 'idle' && (!camera || !endpoint.trim())} onClick={status === 'idle' ? startTransmission : stopTransmission}>{status === 'connecting' ? 'Cancelar conexión' : status === 'live' ? 'Detener transmisión' : '⇄  Iniciar transmisión'}</button>
    <div className="section-label separated"><span>03</span> Visualización</div><div className="range-label"><label htmlFor="confidence">Confianza mínima</label><b>{threshold}%</b></div><input id="confidence" type="range" min="0" max="100" step="1" value={threshold} onChange={e => setThreshold(Number(e.target.value))}/><div className="range-ends"><span>Más detecciones</span><span>Más precisión</span></div></div></section>
    <div className="privacy"><span>◈</span><p><b>Tú controlas la transmisión</b>La cámara solo se envía cuando inicias la transmisión. No se solicita audio.</p></div></aside></div>
    <section className="results"><div className="card-heading"><h2>Detecciones <span className="count">{visible.length}</span></h2><span className="muted">{demo ? 'Datos simulados · sin modelo conectado' : received ? 'Última respuesta del modelo' : status === 'live' ? 'Esperando resultados del modelo' : 'Esperando conexión al modelo'}</span></div>{visible.length ? <div className="detection-list">{visible.map(d => <div className="detection-item" key={d.id}><span className="mini-egg"/><div><b>{d.label}</b><small>ID {d.id} · {d.status}</small></div><strong>{Math.round(d.confidence * 100)}<small>%</small></strong><div className="confidence-track"><span style={{ width: `${d.confidence * 100}%` }}/></div></div>)}</div> : <p className="empty-results">{demo || received ? 'No hay detecciones que superen la confianza mínima.' : 'Las detecciones aparecerán aquí cuando tu modelo analice el video.'}</p>}</section>
    <footer id="guide"><span><b>Un flujo, tres pasos.</b> Activa la cámara <span>→</span> Conecta tu modelo <span>→</span> Observa las detecciones</span><span>HUEVOPS <b> / </b> VISION LAB</span></footer>
    </main></div></div>;
}
