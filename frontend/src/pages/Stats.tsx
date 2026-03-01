import { Loader2, X, ChevronRight } from 'lucide-react';
import { useState, useEffect, useCallback } from 'react';
import { statsApi } from '../services/api';
import { getImageUrl } from '../utils/imageUrl';
import type { Period, Artist, Track, TopAlbum } from '../types';
import { CollageGrid, normalizeItems } from '../components/CollageGrid';
import type { ContentType, OverlayOptions } from '../components/CollageGrid';

const PERIODS: Array<{ key: Period; label: string }> = [
  { key: '7day', label: '7 Days' },
  { key: '1month', label: '1 Month' },
  { key: '3month', label: '3 Months' },
  { key: '6month', label: '6 Months' },
  { key: '12month', label: '12 Months' },
  { key: 'overall', label: 'All Time' },
];

const CONTENT_TYPES: Array<{ key: ContentType; label: string }> = [
  { key: 'artists', label: 'Artists' },
  { key: 'albums', label: 'Albums' },
  { key: 'tracks', label: 'Tracks' },
];

const GRID_LIMIT = 20;
const MODAL_LIMIT = 100;

const LS_KEY = 'stats_prefs';

interface StatsPrefs {
  period: Period;
  contentType: ContentType;
  overlays: OverlayOptions;
}

const VALID_PERIODS: readonly Period[] = ['7day', '1month', '3month', '6month', '12month', 'overall'];
const VALID_CONTENT_TYPES: readonly ContentType[] = ['artists', 'albums', 'tracks'];

function isValidPrefs(v: unknown): v is StatsPrefs {
  if (!v || typeof v !== 'object') return false;
  const p = v as Record<string, unknown>;
  return (
    typeof p.period === 'string' && (VALID_PERIODS as readonly string[]).includes(p.period) &&
    typeof p.contentType === 'string' && (VALID_CONTENT_TYPES as readonly string[]).includes(p.contentType) &&
    p.overlays !== null && typeof p.overlays === 'object'
  );
}

function loadPrefs(): StatsPrefs {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (isValidPrefs(parsed)) return parsed;
    }
  } catch {}
  return {
    period: '1month',
    contentType: 'artists',
    overlays: { showArtist: false, showAlbum: false, showPlaycount: true },
  };
}

function savePrefs(prefs: StatsPrefs) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(prefs));
  } catch {}
}

// ---------------------------------------------------------------------------
// See More Modal
// ---------------------------------------------------------------------------

interface StatsModalProps {
  open: boolean;
  onClose: () => void;
  contentType: ContentType;
  period: Period;
}

function StatsModal({ open, onClose, contentType, period }: StatsModalProps) {
  const [data, setData] = useState<Artist[] | Track[] | TopAlbum[] | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setData(null);
    const fetch = async () => {
      try {
        let result: Artist[] | Track[] | TopAlbum[];
        if (contentType === 'artists') result = await statsApi.getTopArtists(period, MODAL_LIMIT);
        else if (contentType === 'albums') result = await statsApi.getTopAlbums(period, MODAL_LIMIT);
        else result = await statsApi.getTopTracks(period, MODAL_LIMIT);
        setData(result);
      } catch {
        setData([]);
      } finally {
        setLoading(false);
      }
    };
    fetch();
  }, [open, contentType, period]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const items = data ? normalizeItems(data, contentType) : [];
  const label = CONTENT_TYPES.find(c => c.key === contentType)?.label ?? '';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={onClose} />
      <div className="relative bg-[#0d0d0d] border border-[#1a1a1a] w-full max-w-lg mx-4 max-h-[85vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#1a1a1a] flex-shrink-0">
          <span className="text-text-primary text-sm font-semibold">
            Top {label} by Scrobbles
          </span>
          <button onClick={onClose} className="text-text-muted hover:text-text-primary transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* List */}
        <div className="overflow-y-auto flex-1">
          {loading && (
            <div className="flex items-center justify-center py-16">
              <Loader2 size={20} className="animate-spin text-text-muted" />
            </div>
          )}
          {!loading && items.length === 0 && (
            <p className="text-center text-text-muted text-sm py-16">No data for this period.</p>
          )}
          {!loading && items.map((item, i) => (
            <div key={`${item.name}:${item.artist ?? ''}`} className="flex items-center gap-3 px-4 py-2.5 border-b border-[#111] hover:bg-[#111] transition-colors">
              <span className="text-[11px] font-black text-[#444] w-6 text-right flex-shrink-0">{i + 1}</span>
              <div className="w-10 h-10 flex-shrink-0 overflow-hidden bg-[#1a1a1a]">
                {item.image ? (
                  <img src={getImageUrl(item.image)} alt={item.name} loading="lazy" className="w-full h-full object-cover" />
                ) : (
                  <div className="w-full h-full" style={{ background: `hsl(${item.name.charCodeAt(0) % 360},20%,12%)` }} />
                )}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-text-primary text-sm font-medium truncate">{item.name}</p>
                {item.artist && (
                  <p className="text-text-muted text-xs truncate">{item.artist}</p>
                )}
              </div>
              {item.playcount != null && (
                <span className="text-accent text-xs font-medium flex-shrink-0 tabular-nums">
                  {item.playcount.toLocaleString()}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stats page
// ---------------------------------------------------------------------------

export function Stats() {
  const saved = loadPrefs();
  const [period, setPeriod] = useState<Period>(saved.period);
  const [contentType, setContentType] = useState<ContentType>(saved.contentType);
  const [overlays, setOverlays] = useState<OverlayOptions>(saved.overlays);
  const [data, setData] = useState<Artist[] | Track[] | TopAlbum[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [seeMoreOpen, setSeeMoreOpen] = useState(false);

  const fetchData = useCallback(async (p: Period, ct: ContentType) => {
    setLoading(true);
    try {
      let result: Artist[] | Track[] | TopAlbum[];
      if (ct === 'artists') result = await statsApi.getTopArtists(p, GRID_LIMIT);
      else if (ct === 'albums') result = await statsApi.getTopAlbums(p, GRID_LIMIT);
      else result = await statsApi.getTopTracks(p, GRID_LIMIT);
      setData(result);
    } catch {
      setData([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setData(null);
    fetchData(period, contentType);
  }, [period, contentType, fetchData]);

  useEffect(() => {
    savePrefs({ period, contentType, overlays });
  }, [period, contentType, overlays]);

  const handleContentTypeChange = (ct: ContentType) => {
    setData(null);
    setContentType(ct);
  };

  const toggleOverlay = (key: keyof OverlayOptions) => {
    setOverlays(prev => ({ ...prev, [key]: !prev[key] }));
  };

  const items = data ? normalizeItems(data, contentType) : [];

  return (
    <div className="py-8 space-y-6">
      <div>
        <h1 className="page-title">Your Soundtrack</h1>
        <p className="text-text-muted text-sm mt-1">See what you've been listening to</p>
      </div>

      <div className="flex overflow-x-auto scrollbar-hide border-b border-[#1a1a1a]">
        {PERIODS.map(p => (
          <button
            key={p.key}
            onClick={() => setPeriod(p.key)}
            className={`flex-shrink-0 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
              period === p.key
                ? 'border-accent text-accent'
                : 'border-transparent text-text-muted hover:text-text-secondary'
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex gap-1 bg-[#111] p-0.5 rounded-sm">
          {CONTENT_TYPES.map(ct => (
            <button
              key={ct.key}
              onClick={() => handleContentTypeChange(ct.key)}
              className={`px-4 py-1.5 text-sm font-medium rounded-sm transition-colors ${
                contentType === ct.key
                  ? 'bg-[#1e1e1e] text-text-primary'
                  : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              {ct.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1.5">
          {contentType !== 'artists' && (
            <TogglePill
              label="Album"
              active={overlays.showAlbum}
              onClick={() => toggleOverlay('showAlbum')}
            />
          )}
          {contentType !== 'artists' && (
            <TogglePill
              label="Artist"
              active={overlays.showArtist}
              onClick={() => toggleOverlay('showArtist')}
            />
          )}
          <TogglePill
            label="Plays"
            active={overlays.showPlaycount}
            onClick={() => toggleOverlay('showPlaycount')}
          />
        </div>
      </div>

      <CollageGrid
        items={items}
        loading={loading}
        contentType={contentType}
        overlays={overlays}
        skeletonCount={GRID_LIMIT}
        featured
      />

      {!loading && items.length > 0 && (
        <div className="flex justify-center pt-2 pb-4">
          <button
            onClick={() => setSeeMoreOpen(true)}
            className="flex items-center gap-1.5 px-5 py-2 bg-[#141414] hover:bg-[#1a1a1a] border border-[#222] text-text-muted hover:text-text-primary text-sm font-medium transition-colors rounded-sm"
          >
            See More
            <ChevronRight size={14} />
          </button>
        </div>
      )}

      <StatsModal
        open={seeMoreOpen}
        onClose={() => setSeeMoreOpen(false)}
        contentType={contentType}
        period={period}
      />
    </div>
  );
}

interface TogglePillProps {
  label: string;
  active: boolean;
  onClick: () => void;
}

function TogglePill({ label, active, onClick }: TogglePillProps) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1 text-xs font-medium rounded-full transition-colors border ${
        active
          ? 'bg-accent/10 border-accent/40 text-accent'
          : 'bg-transparent border-[#222] text-text-muted hover:text-text-secondary hover:border-[#333]'
      }`}
    >
      {label}
    </button>
  );
}
