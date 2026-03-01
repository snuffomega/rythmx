import { useState, useEffect } from 'react';

// Module-level JS cache — survives page/tab switches without re-fetching.
// Keys: "type:name:artist" (lowercased). Values: resolved image URL.
const _resolved = new Map<string, string>();

export function useImage(
  type: 'artist' | 'album' | 'track',
  name: string,
  artist = '',
  skip = false
): string | null {
  // Guard against undefined/null passed during stale renders (e.g. content-type switches)
  const safeName = name ?? '';
  const safeArtist = artist ?? '';
  const cacheKey = `${type}:${safeName.toLowerCase()}:${safeArtist.toLowerCase()}`;

  const [imageUrl, setImageUrl] = useState<string | null>(
    () => (skip ? null : (_resolved.get(cacheKey) ?? null))
  );

  useEffect(() => {
    if (skip || !safeName) return;
    if (_resolved.has(cacheKey)) {
      setImageUrl(_resolved.get(cacheKey) ?? null);
      return;
    }

    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    interface ResolveResponse {
      image_url?: string;
      pending?: boolean;
    }

    const fetchImage = (attempt = 0) => {
      fetch('/api/images/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, name: safeName, artist: safeArtist }),
      })
        .then(r => r.json() as Promise<ResolveResponse>)
        .then(data => {
          if (cancelled) return;
          if (data.image_url) {
            _resolved.set(cacheKey, data.image_url);
            setImageUrl(data.image_url);
          } else if (data.pending && attempt < 4) {
            // Background fetch in progress — retry with backoff: 3s, 6s, 12s, 24s
            retryTimer = setTimeout(() => fetchImage(attempt + 1), 3000 * (attempt + 1));
          }
        })
        .catch(() => {});
    };

    fetchImage();
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [cacheKey, skip]);

  return imageUrl;
}
