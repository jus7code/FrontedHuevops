# HuevOps: cámara y backend

## Ejecutar

Node.js >= 22.13. Ejecuta `npm install` y `npm run dev`; abre la URL local indicada. La cámara necesita localhost o HTTPS y permiso del usuario. `npm run build` compila y `npm test` verifica el formato de detecciones. `npx tsc --noEmit` comprueba tipos.

## Funcionamiento

- Activa la cámara: la vista es local, sin audio.
- Introduce el endpoint WebSocket y pulsa Iniciar transmisión para enviar video.
- Detener transmisión mantiene la vista local. Detener apaga cámara y conexión.
- La demostración usa ilustraciones y datos simulados, sin inferencia ni envío.
- El conteo es de la última respuesta, no un acumulado de huevos únicos. El umbral solo filtra la visualización. FPS es la tasa configurada de captura, no la velocidad del modelo.

## Contrato del backend recibido

El endpoint se configura con `NEXT_PUBLIC_BACKEND_VIDEO_URL` (ver `.env.example` y `README.md`). Sin esa variable, `lib/backend-config.ts` utiliza `ws://44.199.34.125:8000/video` para desarrollo. En Vercel se necesita una URL `wss://` con TLS configurado. La prueba con cámara y modelo reales requiere que el endpoint y el servicio backend permanezcan activos.

Adaptador: `lib/video-transport.ts`. Validación: `lib/detections.ts`.

MediaRecorder envía video codificado por WebSocket; no se toman fotos ni se envían JPEG. Selecciona exclusivamente WebM/VP8 o WebM/VP9. MP4 no se admite. Antes de conectar se validan el estado de la cámara y sus dimensiones reales, obligatorias y enteras positivas.

Primer mensaje de texto:

```json
{"type":"start","sessionId":"uuid-del-cliente","mimeType":"video/webm;codecs=vp8","width":1280,"height":720,"fps":30,"timesliceMs":200}
```

Después llegan fragmentos binarios aproximadamente cada 200 ms. **Forman un mismo flujo y no son videos independientes.** El backend debe decodificar incrementalmente conservando el orden y la cabecera inicial. Una reconexión inicia otra sesión y otro decodificador. No se descartan fragmentos intermedios: si la cola de envío supera 4 MB se detiene la transmisión y se informa al usuario.

Respuesta de texto JSON:

```json
{"type":"detections","sessionId":"uuid-del-cliente","sequence":1,"detections":[{"id":"egg-1","label":"Huevo","status":"Roto","confidence":0.97,"box":[0.15,0.23,0.17,0.43]}]}
```

- `sequence`: entero creciente por respuesta. Se ignoran resultados anteriores, repetidos o de otra sesión.
- `status`: exactamente `Sano` o `Roto`.
- `box`: `[x, y, ancho, alto]`, normalizados de 0 a 1 respecto al video completo; origen arriba a la izquierda. Sin espejo ni recorte.
- `confidence`: 0 a 1. IDs únicos dentro de cada respuesta.
- `detections: []` limpia las cajas. Sin respuestas durante 1.5 s, se ocultan resultados antiguos.
- Cerrar WebSocket termina la sesión. Reconexión manual.

**No hay sincronización exacta por fotograma todavía**: las cajas representan la última respuesta sobre el video local. La latencia puede desplazarlas respecto a objetos en movimiento. Al recibir el endpoint acordaremos transporte (WebSocket o WebRTC), timestamps, códecs, autenticación y formato de resultados. WebRTC requiere señalización y soporte del servidor.

Una página HTTPS requiere `wss://`. El backend deberá validar Origin y aplicar la autenticación acordada. No incluir secretos del servidor en el frontend.

Fuentes: [MediaRecorder.start](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/start), [WebSocket.bufferedAmount](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket/bufferedAmount).

## Imagen de presentación

`public/og.png` se generó con ImageGen integrado. Brief: tarjeta horizontal HuevOps, fondo blanco roto, tipografía verde bosque, tres huevos con cajas de detección y textos «Cada huevo, a la vista.» y «Visión en tiempo real».

## Historial de fotos

Se inspecciona un huevo a la vez durante 3 segundos desde la primera prediccion valida. Cada respuesta con exactamente un huevo y confianza suficiente suma un voto Sano o Roto. Los IDs pueden cambiar sin abrir una nueva lectura. Al completar la ventana se guarda un unico resultado por mayoria, con votos y la foto de mayor confianza de la clase ganadora. Sano corresponde a Mantener; Roto a Desechar. Un empate, menos de dos votos o la falta de foto requieren retirar y volver a colocar el huevo, sin guardar resultado.

La interfaz indica colocar, mantener quieto con cuenta regresiva y retirar. Tras finalizar, solo se habilita otro huevo al recibir respuestas explicitamente vacias durante al menos un segundo, sin interrupciones de 1.5 segundos. Silencio, baja confianza o cambios de ID no desbloquean la siguiente lectura. Detener/reconectar conserva el bloqueo de una lectura finalizada. Multiples huevos, una respuesta vacia o baja confianza cancelan una lectura en curso; 1.5 segundos sin predicciones validas tambien la cancelan.

Se conservan los 100 resultados recientes en memoria mientras la pagina esta abierta, incluso al detener la camara; se pueden descargar o limpiar. Recargar elimina el historial. Limpiar no desbloquea el huevo ya clasificado. La demo no genera registros. El usuario debe retirar cada huevo antes de presentar el siguiente; no se garantiza identidad fisica si intercambia huevos sin vaciar la vista.

Las fotos son recortes JPEG del video local al recibir predicciones, no del fotograma exacto analizado por el backend. La latencia puede afectar el recorte si el huevo se mueve. La clasificacion final representa la mayoria del modelo, no una garantia de exactitud.
