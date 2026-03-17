import { useState, useEffect, useCallback } from 'react';
import {
  Loader2, Search, Grid3X3, List, RefreshCw,
  Library as LibraryIcon, ChevronLeft, Star,
  ListPlus, MoreHorizontal, User, Disc,
} from 'lucide-react';
import { libraryBrowseApi, libraryApi } from '../services/api';
import { useImage } from '../hooks/useImage';
import { getImageUrl } from '../utils/imageUrl';
import { ApiErrorBanner } from '../components/common';
import type { LibArtist, LibAlbum, LibTrack, LibArtistDetail, LibAlbumDetail, LibraryStatus } from '../types';

type Tab = 'artists' | 'albums' | 'tracks';
type ViewMode = 'grid' | 'list';
type DrillType = 'root' | 'artist-detail' | 'album-detail';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDuration(ms: number | null): string {
  if (!ms) return '--:--';
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function firstTag(json: string | null): string {
  try {
    const tags = JSON.parse(json ?? '[]') as string[];
    return tags[0] ?? '';
  } catch {
    return '';
  }
}

// ---------------------------------------------------------------------------
// StarRating
// ---------------------------------------------------------------------------

interface StarRatingProps {
  value: number;      // 0-10 stored
  onChange?: (v: number) => void;
  size?: number;
  readonly?: boolean;
}

function StarRating({ value, onChange, size = 14, readonly = false }: StarRatingProps) {
  const display = Math.round(value / 2);  // 0-10 → 0-5
  const [hover, setHover] = useState<number | null>(null);
  const active = hover ?? display;

  return (
    <div className="flex items-center gap-0.5" onMouseLeave={() => setHover(null)}>
      {[1, 2, 3, 4, 5].map(star => (
        <button
          key={star}
          type="button"
          disabled={readonly}
          onMouseEnter={() => !readonly && setHover(star)}
          onClick={() => !readonly && onChange?.(star * 2)}
          className={`transition-colors ${readonly ? 'cursor-default' : 'cursor-pointer'}`}
        >
          <Star
            size={size}
            className={star <= active ? 'text-accent' : 'text-text-muted'}
            fill={star <= active ? 'currentColor' : 'none'}
          />
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ArtistImage — thin wrapper so each card gets its own hook instance
// ---------------------------------------------------------------------------

function ArtistImage({ name, size }: { name: string; size: number }) {
  const url = useImage('artist', name);
  if (url) return <img src={getImageUrl(url)} alt={name} className="w-full h-full object-cover" />;
  return <User size={size} className="text-text-muted" />;
}

function AlbumImage({ title, artist, size }: { title: string; artist: string; size: number }) {
  const url = useImage('album', title, artist);
  if (url) return <img src={getImageUrl(url)} alt={title} className="w-full h-full object-cover" />;
  return <Disc size={size} className="text-text-muted" />;
}

// ---------------------------------------------------------------------------
// Source chip + confidence badge
// ---------------------------------------------------------------------------

const BACKEND_COLORS: Record<string, string> = {
  plex: 'bg-orange-500/10 text-orange-400 border-orange-500/20',
  navidrome: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  jellyfin: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
};

function SourceChip({ backend }: { backend: string | null }) {
  if (!backend) return null;
  const cls = BACKEND_COLORS[backend] ?? 'bg-[#1a1a1a] text-text-muted border-[#333]';
  return (
    <span className={`font-mono text-[10px] px-1.5 py-0.5 rounded border ${cls}`}>
      {backend}
    </span>
  );
}

function ConfidenceBadge({ value }: { value: number }) {
  const color = value >= 90 ? 'text-green-400' : value >= 70 ? 'text-yellow-400' : 'text-red-400';
  return <span className={`font-mono text-[10px] ${color}`}>{value}%</span>;
}

// ---------------------------------------------------------------------------
// Artist cards
// ---------------------------------------------------------------------------

interface ArtistCardProps {
  artist: LibArtist;
  onClick: (id: string) => void;
  viewMode: ViewMode;
}

function ArtistCard({ artist, onClick, viewMode }: ArtistCardProps) {
  const genre = firstTag(artist.lastfm_tags_json);

  if (viewMode === 'grid') {
    return (
      <button
        onClick={() => onClick(artist.id)}
        className="text-left group hover:bg-[#111] rounded-sm p-2 transition-colors"
      >
        <div className="aspect-square bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center mb-2 border border-[#222]">
          <ArtistImage name={artist.name} size={32} />
        </div>
        <p className="text-text-primary text-sm font-medium truncate">{artist.name}</p>
        <p className="text-text-muted text-xs font-mono truncate">
          {artist.album_count} album{artist.album_count !== 1 ? 's' : ''}
          {genre && ` · ${genre}`}
        </p>
      </button>
    );
  }

  return (
    <button
      onClick={() => onClick(artist.id)}
      className="w-full flex items-center gap-3 px-2 py-2 hover:bg-[#111] transition-colors rounded-sm"
    >
      <div className="w-10 h-10 flex-shrink-0 bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center border border-[#222]">
        <ArtistImage name={artist.name} size={18} />
      </div>
      <div className="flex-1 min-w-0 text-left">
        <p className="text-text-primary text-sm font-medium truncate">{artist.name}</p>
        <p className="text-text-muted text-xs font-mono">{artist.album_count} albums{genre && ` · ${genre}`}</p>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <ConfidenceBadge value={artist.match_confidence} />
        <SourceChip backend={artist.source_backend} />
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Album cards
// ---------------------------------------------------------------------------

interface AlbumCardProps {
  album: LibAlbum;
  onClick: (id: string, artistId: string) => void;
  viewMode: ViewMode;
}

function AlbumCard({ album, onClick, viewMode }: AlbumCardProps) {
  if (viewMode === 'grid') {
    return (
      <button
        onClick={() => onClick(album.id, album.artist_id)}
        className="text-left group hover:bg-[#111] rounded-sm p-2 transition-colors"
      >
        <div className="aspect-square bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center mb-2 border border-[#222]">
          <AlbumImage title={album.title} artist={album.artist_name} size={32} />
        </div>
        <p className="text-text-primary text-sm font-medium truncate">{album.title}</p>
        <p className="text-text-muted text-xs font-mono truncate">
          {album.artist_name}{album.year && ` · ${album.year}`}
        </p>
      </button>
    );
  }

  return (
    <button
      onClick={() => onClick(album.id, album.artist_id)}
      className="w-full flex items-center gap-3 px-2 py-2 hover:bg-[#111] transition-colors rounded-sm"
    >
      <div className="w-10 h-10 flex-shrink-0 bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center border border-[#222]">
        <AlbumImage title={album.title} artist={album.artist_name} size={18} />
      </div>
      <div className="flex-1 min-w-0 text-left">
        <p className="text-text-primary text-sm font-medium truncate">{album.title}</p>
        <p className="text-text-muted text-xs font-mono truncate">{album.artist_name}</p>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0 text-text-muted text-xs font-mono">
        {album.year && <span>{album.year}</span>}
        {album.record_type && <span className="capitalize">{album.record_type}</span>}
        <SourceChip backend={album.source_backend} />
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Artist detail
// ---------------------------------------------------------------------------

interface ArtistDetailProps {
  artistId: string;
  onAlbumClick: (albumId: string, artistId: string) => void;
  onBack: () => void;
}

function ArtistDetail({ artistId, onAlbumClick, onBack }: ArtistDetailProps) {
  const [data, setData] = useState<LibArtistDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    libraryBrowseApi.getArtist(artistId)
      .then(res => { setData({ artist: res.artist, albums: res.albums, top_tracks: res.top_tracks }); })
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load artist'))
      .finally(() => setLoading(false));
  }, [artistId]);

  if (loading) {
    return <div className="flex-1 flex items-center justify-center"><Loader2 size={20} className="animate-spin text-text-muted" /></div>;
  }
  if (error || !data) {
    return <ApiErrorBanner error={error ?? 'Not found'} onRetry={() => setLoading(true)} />;
  }

  const { artist, albums, top_tracks } = data;
  const genre = firstTag(artist.lastfm_tags_json);
  const totalDuration = top_tracks.reduce((s, t) => s + (t.duration ?? 0), 0);

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Breadcrumb */}
      <div className="px-8 pt-4 pb-2">
        <button onClick={onBack} className="flex items-center gap-1.5 text-text-muted hover:text-text-primary text-xs font-mono uppercase tracking-wider transition-colors">
          <LibraryIcon size={13} /> Library
        </button>
      </div>

      {/* Hero */}
      <div className="px-8 pb-6 flex gap-8">
        <div className="w-48 h-48 flex-shrink-0 bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center border border-[#222]">
          <ArtistImage name={artist.name} size={56} />
        </div>
        <div className="flex flex-col justify-center min-w-0 flex-1">
          <h1 className="text-3xl font-bold tracking-tighter text-text-primary mb-1">{artist.name}</h1>
          {genre && <p className="font-mono text-sm text-text-secondary mb-2">{genre}</p>}
          <div className="flex items-center gap-2 mt-3">
            <ConfidenceBadge value={artist.match_confidence} />
            <SourceChip backend={artist.source_backend} />
            <span className="font-mono text-[10px] text-text-muted">{artist.album_count} albums</span>
          </div>
        </div>
      </div>

      <div className="border-t border-[#1a1a1a]" />

      {/* Top Tracks */}
      {top_tracks.length > 0 && (
        <div className="px-8 py-5">
          <h2 className="text-xs font-mono font-semibold text-text-muted uppercase tracking-widest mb-3">Popular Tracks</h2>
          <div>
            {top_tracks.map((t, i) => (
              <div key={t.id} className="group grid grid-cols-[2rem_1fr_1fr_3.5rem] gap-3 items-center py-2 px-2 hover:bg-[#111] rounded-sm transition-colors">
                <span className="font-mono text-xs text-text-muted tabular-nums text-right">{i + 1}</span>
                <span className="text-sm text-text-primary truncate">{t.title}</span>
                <span className="font-mono text-xs text-text-muted truncate">{t.album_title}</span>
                <span className="font-mono text-xs text-text-muted tabular-nums text-right">{formatDuration(t.duration)}</span>
              </div>
            ))}
          </div>
          {totalDuration > 0 && (
            <p className="font-mono text-[10px] text-text-muted mt-2">{Math.round(totalDuration / 60000)} min total</p>
          )}
        </div>
      )}

      <div className="border-t border-[#1a1a1a]" />

      {/* Albums */}
      <div className="px-8 py-5">
        <h2 className="text-xs font-mono font-semibold text-text-muted uppercase tracking-widest mb-3">{albums.length} Albums</h2>
        <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
          {albums.map(al => (
            <AlbumCard key={al.id} album={al} onClick={(id, aid) => onAlbumClick(id, aid)} viewMode="grid" />
          ))}
        </div>
      </div>

      {/* TODO Phase 14: similar artists (lib_similar_artists table) */}

      <div className="h-4" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Album detail
// ---------------------------------------------------------------------------

interface AlbumDetailProps {
  albumId: string;
  onArtistClick: (artistId: string) => void;
  onBack: () => void;
  onLibrary: () => void;
}

function AlbumDetail({ albumId, onArtistClick, onBack, onLibrary }: AlbumDetailProps) {
  const [data, setData] = useState<LibAlbumDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ratings, setRatings] = useState<Record<string, number>>({});

  useEffect(() => {
    setLoading(true);
    setError(null);
    libraryBrowseApi.getAlbum(albumId)
      .then(res => {
        setData({ album: res.album, tracks: res.tracks });
        const init: Record<string, number> = {};
        res.tracks.forEach(t => { init[t.id] = t.rating; });
        setRatings(init);
      })
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load album'))
      .finally(() => setLoading(false));
  }, [albumId]);

  const handleRate = useCallback(async (trackId: string, rating: number) => {
    setRatings(prev => ({ ...prev, [trackId]: rating }));
    try { await libraryBrowseApi.rateTrack(trackId, rating); } catch { /* optimistic — silently ignore */ }
  }, []);

  if (loading) {
    return <div className="flex-1 flex items-center justify-center"><Loader2 size={20} className="animate-spin text-text-muted" /></div>;
  }
  if (error || !data) {
    return <ApiErrorBanner error={error ?? 'Not found'} onRetry={() => setLoading(true)} />;
  }

  const { album, tracks } = data;
  const totalMs = tracks.reduce((s, t) => s + (t.duration ?? 0), 0);
  const totalMin = Math.round(totalMs / 60000);

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Breadcrumb */}
      <div className="px-8 pt-4 pb-2 flex items-center gap-3">
        <button onClick={onLibrary} className="flex items-center gap-1.5 text-text-muted hover:text-text-primary text-xs font-mono uppercase tracking-wider transition-colors">
          <LibraryIcon size={13} /> Library
        </button>
        <span className="text-[#333]">/</span>
        <button onClick={onBack} className="flex items-center gap-1.5 text-text-muted hover:text-text-primary text-xs font-mono uppercase tracking-wider transition-colors">
          <ChevronLeft size={13} /> Back
        </button>
      </div>

      {/* Hero */}
      <div className="px-8 pb-6 flex gap-8">
        <div className="w-48 h-48 flex-shrink-0 bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center border border-[#222]">
          <AlbumImage title={album.title} artist={album.artist_name} size={56} />
        </div>
        <div className="flex flex-col justify-center min-w-0 flex-1">
          <button
            onClick={() => onArtistClick(album.artist_id)}
            className="font-mono text-base text-accent hover:text-accent/80 transition-colors text-left w-fit mb-1"
          >
            {album.artist_name}
          </button>
          <h1 className="text-3xl font-bold tracking-tighter text-text-primary mb-1">{album.title}</h1>
          <div className="flex items-center gap-3 text-xs font-mono text-text-muted mt-1">
            {album.year && <span>{album.year}</span>}
            {album.record_type && <><span className="text-[#333]">|</span><span className="capitalize">{album.record_type}</span></>}
            <span className="text-[#333]">|</span>
            <span>{tracks.length} tracks</span>
            {totalMin > 0 && <><span className="text-[#333]">|</span><span>{totalMin} min</span></>}
          </div>
          <div className="flex items-center gap-2 mt-3">
            <ConfidenceBadge value={album.match_confidence} />
            <SourceChip backend={album.source_backend} />
            {album.needs_verification === 1 && (
              <span className="font-mono text-[10px] text-yellow-400 border border-yellow-400/30 px-1.5 py-0.5 rounded">verify</span>
            )}
          </div>
        </div>
      </div>

      <div className="border-t border-[#1a1a1a]" />

      {/* Track list */}
      <div className="px-8 py-5">
        <div className="grid grid-cols-[2rem_1fr_6rem_3.5rem_auto_auto] gap-3 items-center px-2 py-1.5 border-b border-[#1a1a1a] mb-1">
          <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest text-right">#</span>
          <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest">Title</span>
          <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest">Rating</span>
          <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest text-right">Dur</span>
          <span />
          <span />
        </div>
        {tracks.map(t => (
          <div key={t.id} className="group grid grid-cols-[2rem_1fr_6rem_3.5rem_auto_auto] gap-3 items-center px-2 py-2 hover:bg-[#111] rounded-sm transition-colors">
            <span className="font-mono text-xs text-text-muted tabular-nums text-right">
              {String(t.track_number ?? 0).padStart(2, '0')}
            </span>
            <span className="text-sm text-text-primary truncate">{t.title}</span>
            <StarRating value={ratings[t.id] ?? 0} onChange={v => handleRate(t.id, v)} size={12} />
            <span className="font-mono text-xs text-text-muted tabular-nums text-right">{formatDuration(t.duration)}</span>
            <button className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-accent transition-all" title="Add to playlist">
              <ListPlus size={14} />
            </button>
            <button className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-text-secondary transition-all" title="More">
              <MoreHorizontal size={14} />
            </button>
          </div>
        ))}
      </div>

      <div className="h-4" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Library page (root)
// ---------------------------------------------------------------------------

export function Library() {
  const [tab, setTab] = useState<Tab>('artists');
  const [viewMode, setViewMode] = useState<ViewMode>('grid');
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // Status banner
  const [status, setStatus] = useState<LibraryStatus | null>(null);
  const [syncing, setSyncing] = useState(false);

  // Drill state
  const [drillType, setDrillType] = useState<DrillType>('root');
  const [drillArtistId, setDrillArtistId] = useState<string | null>(null);
  const [drillAlbumId, setDrillAlbumId] = useState<string | null>(null);
  const [albumArtistId, setAlbumArtistId] = useState<string | null>(null);

  // Data
  const [artists, setArtists] = useState<LibArtist[]>([]);
  const [totalArtists, setTotalArtists] = useState(0);
  const [albums, setAlbums] = useState<LibAlbum[]>([]);
  const [totalAlbums, setTotalAlbums] = useState(0);
  const [tracks, setTracks] = useState<LibTrack[]>([]);
  const [totalTracks, setTotalTracks] = useState(0);

  // Filters
  const [backendFilter, setBackendFilter] = useState('all');
  const [recordTypeFilter, setRecordTypeFilter] = useState('all');

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  // Load status banner
  useEffect(() => {
    libraryApi.getStatus().then(setStatus).catch(() => {});
  }, []);

  // Fetch data when tab / filters / search change
  const fetchArtists = useCallback(async (q: string, backend: string) => {
    setLoading(true);
    setFetchError(null);
    try {
      const res = await libraryBrowseApi.getArtists({ q: q || undefined, backend: backend !== 'all' ? backend : undefined });
      setArtists(res.artists);
      setTotalArtists(res.total);
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : 'Failed to load artists');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchAlbums = useCallback(async (q: string, backend: string, recordType: string) => {
    setLoading(true);
    setFetchError(null);
    try {
      const res = await libraryBrowseApi.getAlbums({
        q: q || undefined,
        backend: backend !== 'all' ? backend : undefined,
        record_type: recordType !== 'all' ? recordType : undefined,
      });
      setAlbums(res.albums);
      setTotalAlbums(res.total);
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : 'Failed to load albums');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchTracks = useCallback(async (q: string) => {
    setLoading(true);
    setFetchError(null);
    try {
      const res = await libraryBrowseApi.getTracks({ q: q || undefined });
      setTracks(res.tracks);
      setTotalTracks(res.total);
    } catch (err) {
      setFetchError(err instanceof Error ? err.message : 'Failed to load tracks');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (drillType !== 'root') return;
    if (tab === 'artists') fetchArtists(debouncedSearch, backendFilter);
    else if (tab === 'albums') fetchAlbums(debouncedSearch, backendFilter, recordTypeFilter);
    else fetchTracks(debouncedSearch);
  }, [tab, drillType, debouncedSearch, backendFilter, recordTypeFilter, fetchArtists, fetchAlbums, fetchTracks]);

  // Sync
  const handleSync = useCallback(async () => {
    setSyncing(true);
    try {
      await libraryApi.sync();
      const s = await libraryApi.getStatus();
      setStatus(s);
    } catch { /* ignore */ }
    finally { setSyncing(false); }
  }, []);

  // Navigation
  const goToArtist = (id: string) => {
    setDrillType('artist-detail');
    setDrillArtistId(id);
    setDrillAlbumId(null);
  };

  const goToAlbum = (albumId: string, fromArtistId?: string) => {
    setAlbumArtistId(fromArtistId ?? drillArtistId);
    setDrillType('album-detail');
    setDrillAlbumId(albumId);
  };

  const goBack = () => {
    if (drillType === 'album-detail' && albumArtistId) {
      setDrillType('artist-detail');
      setDrillArtistId(albumArtistId);
      setDrillAlbumId(null);
    } else {
      goRoot();
    }
  };

  const goRoot = () => {
    setDrillType('root');
    setDrillArtistId(null);
    setDrillAlbumId(null);
    setAlbumArtistId(null);
  };

  const handleTabChange = (t: Tab) => {
    setTab(t);
    goRoot();
    setSearch('');
  };

  // Drill views
  if (drillType === 'artist-detail' && drillArtistId) {
    return (
      <div className="flex flex-col h-full overflow-hidden">
        <ArtistDetail
          artistId={drillArtistId}
          onAlbumClick={(aid, artId) => goToAlbum(aid, artId)}
          onBack={goRoot}
        />
      </div>
    );
  }

  if (drillType === 'album-detail' && drillAlbumId) {
    return (
      <div className="flex flex-col h-full overflow-hidden">
        <AlbumDetail
          albumId={drillAlbumId}
          onArtistClick={goToArtist}
          onBack={goBack}
          onLibrary={goRoot}
        />
      </div>
    );
  }

  // Root view
  const footerText = loading ? null
    : tab === 'artists' ? `${totalArtists} artist${totalArtists !== 1 ? 's' : ''}`
    : tab === 'albums'  ? `${totalAlbums} album${totalAlbums !== 1 ? 's' : ''}`
    :                     `${totalTracks} track${totalTracks !== 1 ? 's' : ''}`;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Status banner */}
      {status && (
        <div className="px-6 py-2.5 bg-[#0a0a0a] border-b border-[#1a1a1a] flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-3">
            <LibraryIcon size={14} className="text-accent flex-shrink-0" />
            <div className="flex items-center gap-2 text-xs font-mono text-text-secondary">
              <span>{status.track_count?.toLocaleString()} tracks</span>
              <span className="text-[#333]">·</span>
              <span>{(status as unknown as { enrich_pct?: number }).enrich_pct ?? 0}% enriched</span>
              {status.last_synced && (
                <>
                  <span className="text-[#333]">·</span>
                  <span className="text-text-muted flex items-center gap-1">
                    <RefreshCw size={10} />
                    {new Date(status.last_synced).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                  </span>
                </>
              )}
            </div>
          </div>
          <button
            onClick={handleSync}
            disabled={syncing}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-[#141414] hover:bg-[#1a1a1a] border border-[#222] text-text-muted hover:text-text-primary rounded-sm transition-colors"
          >
            <RefreshCw size={11} className={syncing ? 'animate-spin' : ''} />
            {syncing ? 'Syncing…' : 'Sync Now'}
          </button>
        </div>
      )}

      {/* Header */}
      <div className="px-6 pt-5 pb-3 border-b border-[#1a1a1a] flex-shrink-0">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h1 className="page-title">Library</h1>
            <p className="text-text-muted text-sm mt-0.5">Browse your music collection</p>
          </div>
        </div>

        {/* Tabs + filters */}
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex gap-1 bg-[#111] p-0.5 rounded-sm">
            {(['artists', 'albums', 'tracks'] as Tab[]).map(t => (
              <button
                key={t}
                onClick={() => handleTabChange(t)}
                className={`px-4 py-1.5 text-sm font-medium rounded-sm transition-colors capitalize ${
                  tab === t ? 'bg-[#1e1e1e] text-text-primary' : 'text-text-muted hover:text-text-secondary'
                }`}
              >
                {t}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1.5 flex-wrap">
            {tab === 'albums' && (
              <select
                value={recordTypeFilter}
                onChange={e => setRecordTypeFilter(e.target.value)}
                className="bg-[#111] border border-[#222] text-text-primary text-xs font-mono rounded-sm px-2 py-1.5 appearance-none cursor-pointer focus:outline-none focus:border-accent"
              >
                <option value="all">All Types</option>
                <option value="album">Albums</option>
                <option value="ep">EPs</option>
                <option value="single">Singles</option>
              </select>
            )}

            {tab !== 'tracks' && (
              <select
                value={backendFilter}
                onChange={e => setBackendFilter(e.target.value)}
                className="bg-[#111] border border-[#222] text-text-primary text-xs font-mono rounded-sm px-2 py-1.5 appearance-none cursor-pointer focus:outline-none focus:border-accent"
              >
                <option value="all">All Sources</option>
                <option value="plex">Plex</option>
                <option value="navidrome">Navidrome</option>
                <option value="jellyfin">Jellyfin</option>
              </select>
            )}

            {tab !== 'tracks' && (
              <div className="flex border border-[#222] rounded-sm overflow-hidden">
                <button
                  onClick={() => setViewMode('grid')}
                  className={`px-2 py-1.5 transition-colors ${viewMode === 'grid' ? 'bg-[#1e1e1e] text-text-primary' : 'text-text-muted hover:text-text-secondary'}`}
                >
                  <Grid3X3 size={14} />
                </button>
                <button
                  onClick={() => setViewMode('list')}
                  className={`px-2 py-1.5 transition-colors ${viewMode === 'list' ? 'bg-[#1e1e1e] text-text-primary' : 'text-text-muted hover:text-text-secondary'}`}
                >
                  <List size={14} />
                </button>
              </div>
            )}

            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
              <input
                type="text"
                placeholder={`Search ${tab}…`}
                value={search}
                onChange={e => setSearch(e.target.value)}
                className="bg-[#111] border border-[#222] text-text-primary placeholder-text-muted rounded-sm pl-8 pr-3 py-1.5 text-xs font-mono w-44 focus:outline-none focus:border-accent transition-colors"
              />
            </div>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {fetchError && (
          <div className="p-4">
            <ApiErrorBanner
              error={fetchError}
              onRetry={() => {
                if (tab === 'artists') fetchArtists(debouncedSearch, backendFilter);
                else if (tab === 'albums') fetchAlbums(debouncedSearch, backendFilter, recordTypeFilter);
                else fetchTracks(debouncedSearch);
              }}
            />
          </div>
        )}

        {loading && (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={20} className="animate-spin text-text-muted" />
          </div>
        )}

        {!loading && !fetchError && tab === 'artists' && (
          viewMode === 'grid' ? (
            <div className="p-4 grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-3">
              {artists.map(a => <ArtistCard key={a.id} artist={a} onClick={goToArtist} viewMode="grid" />)}
            </div>
          ) : (
            <div className="py-2">
              {artists.map(a => <ArtistCard key={a.id} artist={a} onClick={goToArtist} viewMode="list" />)}
            </div>
          )
        )}

        {!loading && !fetchError && tab === 'albums' && (
          viewMode === 'grid' ? (
            <div className="p-4 grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-3">
              {albums.map(al => <AlbumCard key={al.id} album={al} onClick={(id, aid) => goToAlbum(id, aid)} viewMode="grid" />)}
            </div>
          ) : (
            <div className="py-2">
              {albums.map(al => <AlbumCard key={al.id} album={al} onClick={(id, aid) => goToAlbum(id, aid)} viewMode="list" />)}
            </div>
          )
        )}

        {!loading && !fetchError && tab === 'tracks' && (
          <div>
            <div className="grid grid-cols-[2rem_1fr_1fr_1fr_3.5rem] gap-3 px-6 py-2 border-b border-[#111] sticky top-0 bg-[#0d0d0d]">
              <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest text-right">#</span>
              <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest">Title</span>
              <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest">Artist</span>
              <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest">Album</span>
              <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest text-right">Dur</span>
            </div>
            {tracks.map((t, i) => (
              <div key={t.id} className="grid grid-cols-[2rem_1fr_1fr_1fr_3.5rem] gap-3 items-center px-6 py-2 hover:bg-[#111] transition-colors">
                <span className="font-mono text-xs text-text-muted tabular-nums text-right">{String(i + 1).padStart(3, '0')}</span>
                <span className="text-sm text-text-primary truncate">{t.title}</span>
                <span className="font-mono text-xs text-text-secondary truncate">{t.artist_name}</span>
                <span className="font-mono text-xs text-text-muted truncate">{t.album_title}</span>
                <span className="font-mono text-xs text-text-muted tabular-nums text-right">{formatDuration(t.duration)}</span>
              </div>
            ))}
          </div>
        )}

        {!loading && !fetchError && (
          (tab === 'artists' && artists.length === 0) ||
          (tab === 'albums' && albums.length === 0) ||
          (tab === 'tracks' && tracks.length === 0)
        ) && (
          <p className="text-center text-text-muted text-sm py-20">
            {debouncedSearch ? `No ${tab} matching "${debouncedSearch}"` : `No ${tab} in library`}
          </p>
        )}
      </div>

      {/* Footer count */}
      {footerText && (
        <div className="px-6 py-2 border-t border-[#1a1a1a] flex-shrink-0">
          <span className="font-mono text-[10px] text-text-muted">{footerText}</span>
        </div>
      )}

      {/* TODO Phase 14: PlayerBar */}
    </div>
  );
}
