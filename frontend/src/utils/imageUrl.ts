/**
 * Image URL resolver — single control point for switching between Direct CDN
 * and Proxied delivery modes.
 *
 * Phase 1: Direct CDN for legacy URLs.
 * Phase 2: Local hash-aware serving via /api/v1/artwork/{hash}, with CDN fallback.
 */
export function getImageUrl(
  rawUrl: string | undefined | null,
  contentHash?: string | null,
  size = 300
): string {
  const raw = rawUrl ?? '';
  if (raw.startsWith('/api/v1/artwork/')) return raw;

  const hash = (contentHash ?? '').trim().toLowerCase();
  const safeSize = Math.max(32, Math.min(2048, Math.floor(size || 300)));

  if (hash) {
    return `/api/v1/artwork/${encodeURIComponent(hash)}?s=${safeSize}`;
  }

  return raw;
}
