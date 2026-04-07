import { Link } from '@tanstack/react-router';
import { Disc } from 'lucide-react';
import { getImageUrl } from '../../../utils/imageUrl';
import { MissingAlbumCard } from './MissingAlbumCard';
import type { MissingReleaseGroup } from '../../../types';

export interface MissingGroupCardProps {
  group: MissingReleaseGroup;
  onDismiss?: (id: string) => void;
}

export function MissingGroupCard({ group, onDismiss }: MissingGroupCardProps) {
  const primary = group.primary;

  // Single edition — render as regular MissingAlbumCard
  if (group.edition_count === 1) {
    return <MissingAlbumCard release={primary} onDismiss={onDismiss} />;
  }

  // Multi-edition — click-through to primary release detail (no inline accordion)
  const inner = (
    <div className="text-left group relative rounded-sm p-2 opacity-70 hover:opacity-90 transition-opacity">
      <div className="relative aspect-square bg-surface-raised rounded-sm overflow-hidden flex items-center justify-center mb-2 border border-dashed border-border-strong">
        {primary.thumb_url ? (
          <img src={getImageUrl(primary.thumb_url)} alt={primary.album_title}
               className="w-full h-full object-cover" loading="lazy" onError={(e) => { e.currentTarget.style.display = 'none'; }} />
        ) : (
          <Disc size={32} className="text-text-muted" />
        )}
        <span className="absolute top-1 right-1 bg-border-strong text-text-muted text-[9px] font-mono px-1.5 py-0.5 rounded-sm">
          {group.edition_count} editions
        </span>
        {group.owned_count > 0 && (
          <span className="absolute top-1 left-1 bg-green-500/20 text-green-400 text-[9px] font-mono px-1.5 py-0.5 rounded-sm">
            {group.owned_count} owned
          </span>
        )}
      </div>
      <p className="text-text-primary text-sm font-medium truncate">{primary.display_title || primary.album_title}</p>
      {primary.release_date && (
        <p className="text-text-muted text-xs font-mono">{primary.release_date.slice(0, 4)}</p>
      )}
    </div>
  );

  if (primary.id) {
    return <Link to="/library/release/$id" params={{ id: primary.id }}>{inner}</Link>;
  }
  return inner;
}
