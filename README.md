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

El endpoint se configura con `NEXT_PUBLIC_BACKEND_VIDEO_URL`. Copia `.env.example` a `.env.local` para desarrollo. Si no defines la variable, se usa `ws://44.199.34.125:8000/video`. También puedes editar el endpoint desde la interfaz.

## Desplegar en Vercel

1. Importa el repositorio y selecciona la raíz del proyecto. `vercel.json` configura Next.js, `npm ci`, `npm run build` y la salida `.next`.
2. Selecciona Node.js 22.x en los ajustes del proyecto.
3. En Settings → Environment Variables agrega `NEXT_PUBLIC_BACKEND_VIDEO_URL` para Production y Preview con la URL segura real de tu backend, por ejemplo `wss://api.tu-dominio.com/video`.
4. Despliega. Si cambias la variable, vuelve a desplegar: Next.js incorpora su valor al compilar. Esta variable es pública; no pongas secretos en ella.

El backend de inferencia se ejecuta fuera de Vercel. Debe aceptar WebSocket en `/video` y tener TLS configurado: no basta con reemplazar `ws://` por `wss://` en la IP. Una página HTTPS no puede transmitir al endpoint inseguro de desarrollo. Sin backend puedes usar la demostración.

Los comandos predeterminados usan Next.js para Vercel. La configuración anterior de Cloudflare/Sites se conserva con `npm run dev:sites`, `npm run build:sites` y `npm run start:sites`. Los ejemplos D1 y las utilidades de autenticación de Sites no se utilizan en la página actual y no requieren variables en Vercel.

Consulta [INTEGRACION.md](INTEGRACION.md) para el contrato recibido, formato de cajas y limitaciones de sincronización. Falta verificar la inferencia con el backend real en ejecución.
