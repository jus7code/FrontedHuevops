import type { Metadata } from 'next';
import { headers } from 'next/headers';
import './globals.css';
export async function generateMetadata(): Promise<Metadata> {
  const h = await headers();
  const host = h.get('host') || 'localhost:3000';
  const protocol = host.startsWith('localhost') ? 'http' : 'https';
  return {
    metadataBase: new URL(protocol + '://' + host),
    title: 'HuevOps — Visión en tiempo real',
    description: 'Observa huevos con tu cámara y visualiza las detecciones de tu modelo en tiempo real.',
    openGraph: { title: 'HuevOps — Cada huevo, a la vista.', images: ['/og.png'] },
    twitter: { card: 'summary_large_image', images: ['/og.png'] },
  };
}
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="es"><body>{children}</body></html>;
}
