import { useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { MissingAlbumCard } from './MissingAlbumCard';
import type { KindGroup } from '../utils';
import type { MissingAlbum } from '../../../types';

export interface MissingKindGroupProps {
  group: KindGroup<MissingAlbum & { record_type?: string | null }>;
  onDismiss?: (id: string) => void;
}

export function MissingKindGroup({ group, onDismiss }: MissingKindGroupProps) {
  const [open, setOpen] = useState(true);
  return (
    <div className="mb-4 ml-5">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-[10px] font-mono text-text-muted uppercase tracking-wider mb-2 hover:text-text-secondary transition-colors"
      >
        <ChevronRight size={12} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
        {group.label} ({group.items.length})
      </button>
      {open && (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
          {group.items.map(r => (
            <MissingAlbumCard key={r.id || r.deezer_album_id || r.itunes_album_id || r.album_title} release={r} onDismiss={onDismiss} />
          ))}
        </div>
      )}
    </div>
  );
}
