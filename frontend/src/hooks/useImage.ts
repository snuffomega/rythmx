import { useState, useEffect } from 'react';

export function useImage(
  type: 'artist' | 'album' | 'track',
  name: string,
  artist = ''
): string | null {
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!name) return;
    let cancelled = false;
    fetch('/api/images/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, name, artist }),
    })
      .then(r => r.json())
      .then(data => {
        if (!cancelled && data.image_url) setImageUrl(data.image_url);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [type, name, artist]);

  return imageUrl;
}
