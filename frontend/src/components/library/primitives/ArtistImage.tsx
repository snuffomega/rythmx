import { useState } from 'react';
import { User } from 'lucide-react';
import { useImage } from '../../../hooks/useImage';
import { getImageUrl } from '../../../utils/imageUrl';

export function ArtistImage({
  name,
  size,
  imageUrl,
  imageHash,
  matchConfidence,
}: {
  name: string;
  size: number;
  imageUrl?: string | null;
  imageHash?: string | null;
  matchConfidence?: number | null;
}) {
  const [errored, setErrored] = useState(false);
  const hasDirectUrl = !!(imageUrl && imageUrl.startsWith('http'));
  const hasHash = !!imageHash;
  const allowResolverFallback = !hasDirectUrl && !hasHash && (matchConfidence ?? 0) >= 85;
  const resolvedUrl = useImage('artist', name, '', !allowResolverFallback);
  const rawSrc = hasHash ? (imageUrl ?? '') : (hasDirectUrl ? imageUrl : resolvedUrl);
  const src = getImageUrl(rawSrc, imageHash);
  const cdnFallback = hasDirectUrl ? (imageUrl ?? '') : '';
  if (src && !errored) {
    return (
      <img
        src={src}
        alt={name}
        className="w-full h-full object-cover"
        onError={(e) => {
          if (imageHash && cdnFallback && e.currentTarget.src !== cdnFallback) {
            e.currentTarget.src = cdnFallback;
            return;
          }
          setErrored(true);
        }}
      />
    );
  }
  return <User size={size} className="text-text-muted" />;
}
