/**
 * Image URL resolver — single control point for switching between Direct CDN
 * and Proxied delivery modes.
 *
 * Phase 1 (current): Direct — returns rawUrl unchanged.
 * Phase 2 (future):  Proxied — uncomment the proxy line below. Flask proxy
 *                    then adds: Cache-Control: public, max-age=2592000, immutable
 */
export function getImageUrl(rawUrl: string | undefined | null): string {
  if (!rawUrl) return '';
  return rawUrl; // Phase 1: Direct CDN
  // Phase 2: return `/api/images/proxy?url=${encodeURIComponent(rawUrl)}`;
}
