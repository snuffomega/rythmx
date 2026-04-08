import { Link } from '@tanstack/react-router';
import { Play } from 'lucide-react';
import { AlbumImage } from '../primitives/AlbumImage';
import { SourceChip } from '../primitives/SourceChip';
import { firstTag, type ViewMode } from '../utils';
import type { LibAlbum } from '../../../types';

export interface AlbumCardProps {
  album: LibAlbum;
  viewMode: ViewMode;
  onHoverPlay?: (album: LibAlbum) => void;
}

export function AlbumCard({ album, viewMode, onHoverPlay }: AlbumCardProps) {
  const showHoverPlay = Boolean(onHoverPlay);

  function handleHoverPlayClick(event: React.MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.stopPropagation();
    onHoverPlay?.(album);
  }

  if (viewMode === 'grid') {
    return (
      <Link
        to="/library/album/$id"
        params={{ id: album.id }}
        className="text-left group rounded-sm p-2 border border-transparent hover:bg-surface-raised hover:border-accent/40 transition-colors block"
      >
        <div className="relative aspect-square bg-surface-raised rounded-sm overflow-hidden flex items-center justify-center mb-2 border border-border group-hover:border-accent/30 group/album">
          <AlbumImage title={album.title} artist={album.artist_name} size={32} thumbUrl={album.thumb_url} thumbHash={album.thumb_hash} matchConfidence={album.match_confidence} />
          {showHoverPlay && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none opacity-0 group-hover/album:opacity-100 transition-opacity">
              <button
                onClick={handleHoverPlayClick}
                className="pointer-events-auto w-11 h-11 rounded-full bg-accent text-black flex items-center justify-center shadow-lg hover:bg-accent/85 transition-colors"
                aria-label={`Play ${album.title}`}
                title="Play album"
              >
                <Play size={18} className="fill-current" />
              </button>
            </div>
          )}
        </div>
        <p className="text-text-primary text-sm font-medium truncate">{album.title}</p>
        <p className="text-text-muted text-xs font-mono truncate">
          {album.artist_name}
          {album.year ? ` * ${album.year}` : ''}
        </p>
      </Link>
    );
  }

  return (
    <Link
      to="/library/album/$id"
      params={{ id: album.id }}
      className="w-full flex items-center gap-3 px-2 py-2 rounded-sm border border-transparent hover:bg-surface-raised hover:border-border-strong transition-colors"
    >
      <div className="relative w-10 h-10 flex-shrink-0 bg-surface-raised rounded-sm overflow-hidden flex items-center justify-center border border-border group-hover:border-accent/30 group/album">
        <AlbumImage title={album.title} artist={album.artist_name} size={18} thumbUrl={album.thumb_url} thumbHash={album.thumb_hash} matchConfidence={album.match_confidence} />
        {showHoverPlay && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none opacity-0 group-hover/album:opacity-100 transition-opacity">
            <button
              onClick={handleHoverPlayClick}
              className="pointer-events-auto w-7 h-7 rounded-full bg-accent text-black flex items-center justify-center shadow-md hover:bg-accent/85 transition-colors"
              aria-label={`Play ${album.title}`}
              title="Play album"
            >
              <Play size={12} className="fill-current" />
            </button>
          </div>
        )}
      </div>
      <div className="flex-1 min-w-0 text-left">
        <p className="text-text-primary text-sm font-medium truncate">{album.title}</p>
        <p className="text-text-muted text-xs font-mono truncate">
          {album.artist_name}
          {album.year ? ` * ${album.year}` : ''}
        </p>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0 text-text-muted text-xs font-mono">
        {album.record_type && <span className="capitalize">{album.record_type}</span>}
        <SourceChip backend={album.source_platform} />
      </div>
    </Link>
  );
}
