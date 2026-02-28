import { Loader2 } from 'lucide-react';
import { useState, useEffect, useCallback } from 'react';
import { statsApi } from '../services/api';
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

const INITIAL_LIMIT = 20;
const LOAD_MORE_STEP = 15;
const MAX_LIMIT = 50;

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

export function Stats() {
  const saved = loadPrefs();
  const [period, setPeriod] = useState<Period>(saved.period);
  const [contentType, setContentType] = useState<ContentType>(saved.contentType);
  const [overlays, setOverlays] = useState<OverlayOptions>(saved.overlays);
  const [limit, setLimit] = useState(INITIAL_LIMIT);
  const [data, setData] = useState<Artist[] | Track[] | TopAlbum[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  const fetchData = useCallback(async (p: Period, ct: ContentType, l: number, isLoadMore = false) => {
    if (isLoadMore) setLoadingMore(true);
    else setLoading(true);

    try {
      let result: Artist[] | Track[] | TopAlbum[];
      if (ct === 'artists') result = await statsApi.getTopArtists(p, l);
      else if (ct === 'albums') result = await statsApi.getTopAlbums(p, l);
      else result = await statsApi.getTopTracks(p, l);
      setData(result);
    } catch {
      setData([]);
    } finally {
      if (isLoadMore) setLoadingMore(false);
      else setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLimit(INITIAL_LIMIT);
    setData(null);
    fetchData(period, contentType, INITIAL_LIMIT);
  }, [period, contentType, fetchData]);

  useEffect(() => {
    savePrefs({ period, contentType, overlays });
  }, [period, contentType, overlays]);

  const handlePeriodChange = (p: Period) => {
    setPeriod(p);
  };

  const handleContentTypeChange = (ct: ContentType) => {
    setData(null);  // Clear stale data in the same batch to avoid type mismatch on next render
    setContentType(ct);
  };

  const toggleOverlay = (key: keyof OverlayOptions) => {
    setOverlays(prev => ({ ...prev, [key]: !prev[key] }));
  };

  const handleLoadMore = async () => {
    const newLimit = Math.min(limit + LOAD_MORE_STEP, MAX_LIMIT);
    setLimit(newLimit);
    await fetchData(period, contentType, newLimit, true);
  };

  const items = data ? normalizeItems(data, contentType) : [];
  const canLoadMore = !loading && data !== null && data.length >= limit && limit < MAX_LIMIT;

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
            onClick={() => handlePeriodChange(p.key)}
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
        skeletonCount={INITIAL_LIMIT}
      />

      {canLoadMore && (
        <div className="flex justify-center pt-2 pb-4">
          <button
            onClick={handleLoadMore}
            disabled={loadingMore}
            className="flex items-center gap-2 px-6 py-2 bg-[#141414] hover:bg-[#1a1a1a] border border-[#222] text-text-secondary hover:text-text-primary text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed rounded-sm"
          >
            {loadingMore ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                Loading...
              </>
            ) : (
              `Load More (${Math.min(limit + LOAD_MORE_STEP, MAX_LIMIT) - limit} more)`
            )}
          </button>
        </div>
      )}

      {!canLoadMore && !loading && data !== null && data.length > 0 && limit >= MAX_LIMIT && (
        <p className="text-center text-[#333] text-xs pb-4">Showing top {data.length}</p>
      )}
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
