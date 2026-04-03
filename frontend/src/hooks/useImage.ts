import { useState, useEffect } from 'react';
import type { ImageType, ImageResolveResponse } from '../types';
import { getApiKey } from '../services/api';
import { getImageUrl } from '../utils/imageUrl';

// Module-level JS cache — survives page/tab switches without re-fetching.
// Keys: "type:name:artist" (lowercased). Values: resolved image URL.
const _resolved = new Map<string, string>();
const _pendingBatch = new Map<string, {
  id: string;
  type: ImageType;
  name: string;
  artist: string;
  resolvers: Array<(result: ImageResolveResponse) => void>;
}>();
let _batchTimer: ReturnType<typeof setTimeout> | null = null;

function _queueImageResolveBatch(
  id: string,
  type: ImageType,
  name: string,
  artist: string
): Promise<ImageResolveResponse> {
  return new Promise((resolve) => {
    const existing = _pendingBatch.get(id);
    if (existing) {
      existing.resolvers.push(resolve);
    } else {
      _pendingBatch.set(id, {
        id,
        type,
        name,
        artist,
        resolvers: [resolve],
      });
    }

    if (_batchTimer) return;
    _batchTimer = setTimeout(() => {
      _batchTimer = null;
      const entries = Array.from(_pendingBatch.values());
      _pendingBatch.clear();
      if (entries.length === 0) return;

      fetch('/api/v1/images/resolve-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Api-Key': getApiKey() },
        body: JSON.stringify({
          items: entries.map((e) => ({
            id: e.id,
            type: e.type,
            name: e.name,
            artist: e.artist,
          })),
        }),
      })
        .then(r => r.json() as Promise<{ items?: Array<ImageResolveResponse & { id?: string }> }>)
        .then((data) => {
          const byId = new Map<string, ImageResolveResponse>();
          for (const item of (data.items ?? [])) {
            const itemId = String(item.id ?? '');
            byId.set(itemId, {
              image_url: item.image_url,
              content_hash: item.content_hash,
              pending: item.pending,
            });
          }

          for (const entry of entries) {
            const result = byId.get(entry.id) ?? { image_url: '', pending: false };
            for (const resolver of entry.resolvers) resolver(result);
          }
        })
        .catch(() => {
          for (const entry of entries) {
            for (const resolver of entry.resolvers) resolver({ image_url: '', pending: false });
          }
        });
    }, 25);
  });
}

export function useImage(
  type: ImageType,
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

    const fetchImage = (attempt = 0) => {
      _queueImageResolveBatch(cacheKey, type, safeName, safeArtist)
        .then(data => {
          if (cancelled) return;
          if (data.image_url) {
            const resolved = data.content_hash
              ? getImageUrl(data.image_url, data.content_hash)
              : data.image_url;
            _resolved.set(cacheKey, resolved);
            setImageUrl(resolved);
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
