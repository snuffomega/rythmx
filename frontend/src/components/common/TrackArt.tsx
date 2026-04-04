import { Disc } from 'lucide-react';
import { useImage } from '../../hooks/useImage';
import { getImageUrl } from '../../utils/imageUrl';

interface TrackArtProps {
  thumbUrl: string | null;
  thumbHash?: string | null;
  title: string;
  artist: string;
  /**
   * sm  — 64×64px  (PlayerBar)
   * lg  — max-w-[400px] aspect-square  (FullPagePlayer)
   * fill — w-full h-full, parent controls dimensions  (VinylPlayerScreen)
   */
  size?: 'sm' | 'lg' | 'fill';
  /** Disc icon pixel size override (fill mode only, defaults to 48) */
  discSize?: number;
  draggable?: boolean;
}

export function TrackArt({
  thumbUrl,
  thumbHash,
  title,
  artist,
  size = 'lg',
  discSize,
  draggable,
}: TrackArtProps) {
  const resolved = useImage('album', title, artist);
  const src = thumbUrl
    ? getImageUrl(thumbUrl, thumbHash ?? null)
    : (resolved ? getImageUrl(resolved) : null);

  if (src) {
    const imgClass =
      size === 'sm'
        ? 'w-16 h-16 object-cover rounded-sm flex-shrink-0'
        : size === 'fill'
          ? 'w-full h-full object-cover'
          : 'w-full max-w-[400px] aspect-square object-cover rounded border border-[#222]';
    return <img src={src} alt={title} className={imgClass} draggable={draggable} />;
  }

  if (size === 'sm') {
    return (
      <div className="w-16 h-16 bg-[#1a1a1a] rounded-sm flex-shrink-0 flex items-center justify-center border border-[#222]">
        <Disc size={24} className="text-text-muted" />
      </div>
    );
  }

  if (size === 'fill') {
    return (
      <div className="w-full h-full bg-[#0f0f0f] flex items-center justify-center">
        <Disc size={discSize ?? 48} className="text-[#252525]" />
      </div>
    );
  }

  return (
    <div className="w-full max-w-[400px] aspect-square bg-[#1a1a1a] rounded flex items-center justify-center border border-[#222]">
      <Disc size={80} className="text-[#333]" />
    </div>
  );
}
