import { Link } from '@tanstack/react-router';
import { Shuffle } from 'lucide-react';
import { ArtistImage } from '../primitives/ArtistImage';
import { ConfidenceBadge } from '../primitives/ConfidenceBadge';
import { SourceChip } from '../primitives/SourceChip';
import { firstTag, type ViewMode } from '../utils';
import type { LibArtist } from '../../../types';

export interface ArtistCardProps {
  artist: LibArtist;
  viewMode: ViewMode;
  onShufflePlay?: (artist: LibArtist) => void;
}

export function ArtistCard({ artist, viewMode, onShufflePlay }: ArtistCardProps) {
  const genre = firstTag(artist.lastfm_tags_json);
  const showHoverPlay = Boolean(onShufflePlay);

  function handleShufflePlayClick(event: React.MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.stopPropagation();
    onShufflePlay?.(artist);
  }

  if (viewMode === 'grid') {
    return (
      <Link
        to="/library/artist/$id"
        params={{ id: artist.id }}
        className="text-left group hover:bg-surface rounded-sm p-2 transition-colors block"
      >
        <div className="relative aspect-square bg-surface-raised rounded-sm overflow-hidden flex items-center justify-center mb-2 border border-border group/art">
          <ArtistImage name={artist.name} size={32} imageUrl={artist.image_url} imageHash={artist.image_hash} matchConfidence={artist.match_confidence} />
          {showHoverPlay && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none opacity-0 group-hover/art:opacity-100 transition-opacity">
              <button
                onClick={handleShufflePlayClick}
                className="pointer-events-auto w-11 h-11 rounded-full bg-accent text-black flex items-center justify-center shadow-lg hover:bg-accent/85 transition-colors"
                aria-label={`Shuffle play ${artist.name}`}
                title="Shuffle play"
              >
                <Shuffle size={18} />
              </button>
            </div>
          )}
        </div>
        <p className="text-text-primary text-sm font-medium truncate">{artist.name}</p>
        <p className="text-text-muted text-xs font-mono truncate">
          {artist.album_count} album{artist.album_count !== 1 ? 's' : ''}
          {genre && ` · ${genre}`}
        </p>
        {artist.missing_count > 0 && (
          <p className="text-[10px] font-mono text-amber-400 mt-0.5">{artist.missing_count} missing</p>
        )}
      </Link>
    );
  }

  return (
    <Link
      to="/library/artist/$id"
      params={{ id: artist.id }}
      className="w-full flex items-center gap-3 px-2 py-2 hover:bg-surface transition-colors rounded-sm"
    >
      <div className="relative w-10 h-10 flex-shrink-0 bg-surface-raised rounded-sm overflow-hidden flex items-center justify-center border border-border group/art">
        <ArtistImage name={artist.name} size={18} imageUrl={artist.image_url} imageHash={artist.image_hash} matchConfidence={artist.match_confidence} />
        {showHoverPlay && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none opacity-0 group-hover/art:opacity-100 transition-opacity">
            <button
              onClick={handleShufflePlayClick}
              className="pointer-events-auto w-7 h-7 rounded-full bg-accent text-black flex items-center justify-center shadow-md hover:bg-accent/85 transition-colors"
              aria-label={`Shuffle play ${artist.name}`}
              title="Shuffle play"
            >
              <Shuffle size={12} />
            </button>
          </div>
        )}
      </div>
      <div className="flex-1 min-w-0 text-left">
        <p className="text-text-primary text-sm font-medium truncate">{artist.name}</p>
        <p className="text-text-muted text-xs font-mono">{artist.album_count} albums{genre && ` · ${genre}`}</p>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        {artist.missing_count > 0 && (
          <span className="font-mono text-[10px] text-amber-400 bg-amber-400/10 px-1.5 py-0.5 rounded border border-amber-400/20">
            {artist.missing_count}
          </span>
        )}
        <ConfidenceBadge value={artist.match_confidence} />
        <SourceChip backend={artist.source_platform} />
      </div>
    </Link>
  );
}
