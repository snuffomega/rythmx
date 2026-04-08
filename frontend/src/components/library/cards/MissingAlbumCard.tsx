import { Link } from '@tanstack/react-router';
import { Disc, X } from 'lucide-react';
import { getImageUrl } from '../../../utils/imageUrl';
import type { MissingAlbum } from '../../../types';

export interface MissingAlbumCardProps {
  release: MissingAlbum;
  onDismiss?: (id: string) => void;
}

export function MissingAlbumCard({ release, onDismiss }: MissingAlbumCardProps) {
  const inner = (
    <div className="text-left group relative rounded-sm p-2 opacity-70 hover:opacity-90 transition-opacity">
      {onDismiss && release.id && (
        <button
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onDismiss(release.id!); }}
          className="absolute top-3 left-3 z-10 opacity-0 group-hover:opacity-100 bg-black/70 hover:bg-[#BA0000]/80 text-text-muted hover:text-white rounded-full p-0.5 transition-all"
          aria-label="Dismiss"
        >
          <X size={12} />
        </button>
      )}
      <div className="relative aspect-square bg-surface-raised rounded-sm overflow-hidden flex items-center justify-center mb-2 border border-dashed border-border-strong">
        {release.thumb_url ? (
          <img src={getImageUrl(release.thumb_url)} alt={release.album_title}
               className="w-full h-full object-cover" loading="lazy" onError={(e) => { e.currentTarget.style.display = 'none'; }} />
        ) : (
          <Disc size={32} className="text-text-muted" />
        )}
        <span className="absolute top-1 right-1 bg-border-strong text-text-muted text-[9px] font-mono px-1.5 py-0.5 rounded-sm uppercase">
          Missing
        </span>
      </div>
      <p className="text-text-primary text-sm font-medium truncate">{release.display_title || release.album_title}</p>
      <div className="flex items-center gap-1.5">
        {release.release_date && (
          <span className="text-text-muted text-xs font-mono">{release.release_date.slice(0, 4)}</span>
        )}
        {release.version_type && release.version_type !== 'original' && (
          <span className="text-[9px] font-mono text-text-muted/70 px-1 py-0.5 bg-surface-raised rounded-sm">{release.version_type}</span>
        )}
      </div>
    </div>
  );
  if (release.id) {
    return <Link to="/library/release/$id" params={{ id: release.id }}>{inner}</Link>;
  }
  return inner;
}
