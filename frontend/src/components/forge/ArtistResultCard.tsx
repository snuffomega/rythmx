import { Users } from 'lucide-react';
import { getImageUrl } from '../../utils/imageUrl';
import type { ForgeDiscoveryResult } from '../../types';

export function ArtistResultCard({ result }: { result: ForgeDiscoveryResult }) {
  const hue = result.artist.charCodeAt(0) % 360;
  return (
    <div className="group flex items-center gap-3 px-4 py-3 bg-base border border-border-subtle hover:border-border-input transition-colors cursor-pointer">
      <div className="w-12 h-12 flex-shrink-0 overflow-hidden">
        {result.image ? (
          <img
            src={getImageUrl(result.image)}
            alt={result.artist}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
            onError={e => { e.currentTarget.style.display = 'none'; }}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center" style={{ background: `hsl(${hue},30%,12%)` }}>
            <Users size={18} className="text-text-dim" />
          </div>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-text-primary text-sm font-semibold truncate">{result.artist}</p>
        {result.track_name && (
          <p className="text-text-primary/80 text-xs truncate mt-0.5">
            Track: {result.track_name}
          </p>
        )}
        {result.album_name && (
          <p className="text-text-muted text-xs truncate mt-0.5">
            Album: {result.album_name}
          </p>
        )}
        {result.reason && <p className="text-text-dim text-xs truncate mt-0.5">{result.reason}</p>}
        {result.tags && result.tags.length > 0 && (
          <div className="flex gap-1 mt-1 flex-wrap">
            {result.tags.slice(0, 3).map(t => (
              <span key={t} className="text-[10px] px-1.5 py-0.5 bg-surface-skeleton border border-border text-text-muted uppercase tracking-wide">{t}</span>
            ))}
          </div>
        )}
      </div>
      {(result.similarity != null || result.rank_position != null || result.is_owned !== undefined) && (
        <div className="flex-shrink-0 text-right">
          {result.similarity != null && (
            <>
              <div className="text-accent text-sm font-bold tabular-nums">{Math.round(result.similarity * 100)}%</div>
              <div className="text-text-dim text-[10px] uppercase tracking-wide">match</div>
            </>
          )}
          {result.rank_position != null && (
            <div className="text-text-muted text-[10px] uppercase tracking-wide mt-1">
              rank #{result.rank_position}
            </div>
          )}
          {result.is_owned !== undefined && (
            <div className={`text-[10px] uppercase tracking-wide mt-1 ${result.is_owned ? 'text-success' : 'text-text-muted'}`}>
              {result.is_owned ? 'owned' : 'missing'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
