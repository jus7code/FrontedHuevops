# HuevOps

Frontend React + TypeScript con cámara web, transmisión continua de video y cajas de detección del backend.

```sh
npm install
npm run dev
```

Abre la URL local indicada y activa la cámara. Puedes explorar la demostración sin backend.

- `npm run build`: compilación de producción.
- `npm test`: validación de respuestas de detección.
- `npx tsc --noEmit`: verificación de tipos.

El endpoint está escrito en `lib/backend-config.ts`: `ws://44.199.34.125:8000/video`, sin variables de entorno ni exclusiones de Git. Inicia el backend en ese puerto, activa la cámara y pulsa «Iniciar transmisión».

Consulta [INTEGRACION.md](INTEGRACION.md) para el contrato recibido, formato de cajas y limitaciones de sincronización. Falta verificar la inferencia con el backend real en ejecución.
