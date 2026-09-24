// Next.js incorpora esta variable pública al compilar el frontend.
export const BACKEND_VIDEO_URL =
  process.env.NEXT_PUBLIC_BACKEND_VIDEO_URL?.trim() || 'ws://44.199.34.125:8000/video';
