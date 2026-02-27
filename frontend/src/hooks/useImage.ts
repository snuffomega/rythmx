import { useState, useEffect } from 'react';

// Module-level JS cache — survives page/tab switches without re-fetching.
// Keys: "type:name:artist" (lowercased). Values: resolved image URL.
const _resolved = new Map<string, string>();

export function useImage(
  type: 'artist' | 'album' | 'track',
  name: string,
  artist = ''
): string | null {
  const cacheKey = `${type}:${name.toLowerCase()}:${artist.toLowerCase()}`;

  const [imageUrl, setImageUrl] = useState<string | null>(
    () => _resolved.get(cacheKey) ?? null
  );

  useEffect(() => {
    if (!name) return;
    if (_resolved.has(cacheKey)) return; // Already resolved this session — no fetch needed

    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const fetchImage = (attempt = 0) => {
      fetch('/api/images/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, name, artist }),
      })
        .then(r => r.json())
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
  }, [cacheKey]);

  return imageUrl;
}
