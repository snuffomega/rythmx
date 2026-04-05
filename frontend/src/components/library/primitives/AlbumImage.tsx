import { useState } from 'react';
import { Disc } from 'lucide-react';
import { useImage } from '../../../hooks/useImage';
import { getImageUrl } from '../../../utils/imageUrl';

export function AlbumImage({
  title,
  artist,
  size,
  thumbUrl,
  thumbHash,
  matchConfidence,
}: {
  title: string;
  artist: string;
  size: number;
  thumbUrl?: string | null;
  thumbHash?: string | null;
  matchConfidence?: number | null;
}) {
  const [errored, setErrored] = useState(false);
  const hasDirectUrl = !!(thumbUrl && thumbUrl.startsWith('http'));
  const hasHash = !!thumbHash;
  const allowResolverFallback = !hasDirectUrl && !hasHash && (matchConfidence ?? 0) >= 85;
  const resolvedUrl = useImage('album', title, artist, !allowResolverFallback);
  const rawSrc = hasHash ? (thumbUrl ?? '') : (hasDirectUrl ? thumbUrl : resolvedUrl);
  const src = getImageUrl(rawSrc, thumbHash);
  const cdnFallback = hasDirectUrl ? (thumbUrl ?? '') : '';
  if (src && !errored) {
    return (
      <img
        src={src}
        alt={title}
        className="w-full h-full object-cover"
        onError={(e) => {
          if (thumbHash && cdnFallback && e.currentTarget.src !== cdnFallback) {
            e.currentTarget.src = cdnFallback;
            return;
          }
          setErrored(true);
        }}
      />
    );
  }
  return <Disc size={size} className="text-text-muted" />;
}
