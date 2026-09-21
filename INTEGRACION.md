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

Integrado conforme a `INTEGRACION_FRONTEND.txt`. La dirección está directamente en `lib/backend-config.ts`: `wss://receiver-libraries-outline-evident.trycloudflare.com/video`. Es un túnel seguro temporal proporcionado por el backend; la prueba con cámara y modelo reales requiere que el túnel y el servicio backend permanezcan activos.

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
