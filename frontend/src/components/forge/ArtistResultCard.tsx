import { Users } from 'lucide-react';
import { getImageUrl } from '../../utils/imageUrl';
import type { PersonalDiscoveryResult } from '../../types';

export function ArtistResultCard({ result }: { result: PersonalDiscoveryResult }) {
  const hue = result.artist.charCodeAt(0) % 360;
  return (
    <div className="group flex items-center gap-3 px-4 py-3 bg-[#0d0d0d] border border-[#1a1a1a] hover:border-[#2a2a2a] transition-colors cursor-pointer">
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
            <Users size={18} className="text-[#444]" />
          </div>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-text-primary text-sm font-semibold truncate">{result.artist}</p>
        {result.reason && <p className="text-[#444] text-xs truncate mt-0.5">{result.reason}</p>}
        {result.tags && result.tags.length > 0 && (
          <div className="flex gap-1 mt-1 flex-wrap">
            {result.tags.slice(0, 3).map(t => (
              <span key={t} className="text-[10px] px-1.5 py-0.5 bg-[#161616] border border-[#222] text-[#555] uppercase tracking-wide">{t}</span>
            ))}
          </div>
        )}
      </div>
      {result.similarity !== undefined && (
        <div className="flex-shrink-0 text-right">
          <div className="text-accent text-sm font-bold tabular-nums">{Math.round(result.similarity * 100)}%</div>
          <div className="text-[#444] text-[10px] uppercase tracking-wide">match</div>
        </div>
      )}
    </div>
  );
}
