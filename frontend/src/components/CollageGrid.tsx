import { Users, Music2, Disc3 } from 'lucide-react';
import type { Artist, Track, TopAlbum } from '../types';
import { useImage } from '../hooks/useImage';

export type ContentType = 'artists' | 'albums' | 'tracks';

export interface OverlayOptions {
  showArtist: boolean;
  showAlbum: boolean;
  showPlaycount: boolean;
}

interface CollageItem {
  name: string;
  artist?: string;
  image?: string;
  playcount?: number;
}

interface CollageGridProps {
  items: CollageItem[];
  loading: boolean;
  contentType: ContentType;
  overlays: OverlayOptions;
  skeletonCount?: number;
}

function FallbackIcon({ type }: { type: ContentType }) {
  const cls = 'text-[#2a2a2a]';
  if (type === 'artists') return <Users size={28} className={cls} />;
  if (type === 'albums') return <Disc3 size={28} className={cls} />;
  return <Music2 size={28} className={cls} />;
}

export function normalizeItems(
  data: Artist[] | Track[] | TopAlbum[],
  type: ContentType
): CollageItem[] {
  if (type === 'artists') {
    return (data as Artist[]).map(a => ({
      name: a.name,
      image: a.image,
      playcount: a.playcount,
    }));
  }
  if (type === 'albums') {
    return (data as TopAlbum[]).map(a => ({
      name: a.title,
      artist: a.artist,
      image: a.image,
      playcount: a.playcount,
    }));
  }
  return (data as Track[]).map(t => ({
    name: t.name,
    artist: t.artist,
    image: t.image,
    playcount: t.playcount,
  }));
}

export function CollageGrid({ items, loading, contentType, overlays, skeletonCount = 20 }: CollageGridProps) {
  if (loading) {
    return (
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-1.5">
        {Array.from({ length: skeletonCount }).map((_, i) => (
          <div key={i} className="aspect-square bg-[#141414] animate-pulse" />
        ))}
      </div>
    );
  }

  if (!items || items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <FallbackIcon type={contentType} />
        <p className="text-text-muted text-sm mt-4">No data for this period.</p>
        <p className="text-[#333] text-xs mt-1">Connect Last.fm in Settings to get started.</p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-1.5">
      {items.map((item, i) => (
        <CollageCard
          key={i}
          item={item}
          rank={i + 1}
          contentType={contentType}
          overlays={overlays}
        />
      ))}
    </div>
  );
}

interface CollageCardProps {
  item: CollageItem;
  rank: number;
  contentType: ContentType;
  overlays: OverlayOptions;
}

function CollageCard({ item, rank, contentType, overlays }: CollageCardProps) {
  const entityType = contentType === 'artists' ? 'artist' : contentType === 'albums' ? 'album' : 'track';
  const resolvedImage = useImage(entityType, item.name, item.artist ?? '');
  const src = item.image || resolvedImage;

  const hasOverlay = (overlays.showArtist && item.artist) ||
    (overlays.showAlbum && contentType !== 'artists') ||
    overlays.showPlaycount;

  const showArtistLine = overlays.showArtist && item.artist;
  const showAlbumLine = overlays.showAlbum && contentType !== 'artists';
  const showPlaycountLine = overlays.showPlaycount && item.playcount != null;

  return (
    <div className="group relative aspect-square overflow-hidden bg-[#141414] cursor-pointer">
      {src ? (
        <img
          src={src}
          alt={item.name}
          loading="lazy"
          className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-105"
        />
      ) : (
        <div className="w-full h-full flex items-center justify-center">
          <FallbackIcon type={contentType} />
        </div>
      )}

      <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-200" />

      <span className="absolute top-1.5 left-1.5 text-[10px] font-black text-white/80 bg-black/60 px-1.5 py-0.5 leading-none">
        {rank}
      </span>

      {hasOverlay && (
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/85 via-black/50 to-transparent px-2 pt-4 pb-2">
          {showAlbumLine && (
            <p className="text-white text-[11px] font-semibold leading-tight truncate">
              {item.name}
            </p>
          )}
          {showArtistLine && (
            <p className="text-white/70 text-[10px] leading-tight truncate mt-0.5">
              {item.artist}
            </p>
          )}
          {!showAlbumLine && !showArtistLine && showPlaycountLine && (
            <p className="text-white text-[11px] font-semibold leading-tight truncate">
              {item.name}
            </p>
          )}
          {showPlaycountLine && (
            <p className="text-accent text-[10px] font-medium mt-0.5">
              {item.playcount?.toLocaleString()} plays
            </p>
          )}
        </div>
      )}

      <div className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-200 flex flex-col justify-end p-2 pointer-events-none">
        {!hasOverlay && (
          <>
            <p className="text-white text-[11px] font-semibold leading-tight truncate drop-shadow-lg">
              {item.name}
            </p>
            {item.artist && (
              <p className="text-white/70 text-[10px] leading-tight truncate drop-shadow-lg">
                {item.artist}
              </p>
            )}
            {item.playcount != null && (
              <p className="text-accent text-[10px] font-medium drop-shadow-lg">
                {item.playcount.toLocaleString()} plays
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
