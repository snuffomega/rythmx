import { useState, useEffect, useCallback } from 'react';
import {
  Loader2, Search, Grid3X3, List, RefreshCw,
  Library as LibraryIcon, ListPlus, Play,
} from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { libraryBrowseApi, libraryApi, enrichmentApi } from '../../services/api';
import { usePlayerStore, type PlayerTrack } from '../../stores/usePlayerStore';
import { useToastStore } from '../../stores/useToastStore';
import { ApiErrorBanner, PlaylistPickerModal } from '../common';
import { usePlaylistPicker } from '../../hooks/usePlaylistPicker';
import type {
  LibArtist,
  LibAlbum,
  LibTrack,
  LibraryStatus,
} from '../../types';
import {
  type Tab, type ViewMode,
  formatDuration,
} from './utils';
import { ArtistCard } from './cards/ArtistCard';
import { AlbumCard } from './cards/AlbumCard';

// ---------------------------------------------------------------------------
// Library page (root)
// ---------------------------------------------------------------------------

const AZ_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ#'.split('');


export function LibraryRoot() {
  const navigate = useNavigate();
  const toastError = useToastStore(s => s.error);
  const [tab, setTab] = useState<Tab>('artists');
  const [viewMode, setViewMode] = useState<ViewMode>('grid');
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // Status banner
  const [status, setStatus] = useState<LibraryStatus | null>(null);
  const [syncing, setSyncing] = useState(false);

  // Data
  const [artists, setArtists] = useState<LibArtist[]>([]);
  const [totalArtists, setTotalArtists] = useState(0);
  const [albums, setAlbums] = useState<LibAlbum[]>([]);
  const [totalAlbums, setTotalAlbums] = useState(0);
  const [tracks, setTracks] = useState<LibTrack[]>([]);
  const [totalTracks, setTotalTracks] = useState(0);

  // Add-to-playlist modal (tracks tab)
  const picker = usePlaylistPicker();
  const playQueue = usePlayerStore(s => s.playQueue);

  // Filters
  const [backendFilter, setBackendFilter] = useState('all');
  const [recordTypeFilter, setRecordTypeFilter] = useState('all');
  const [decadeFilter, setDecadeFilter] = useState<number | null>(null);
  const [regionFilter, setRegionFilter] = useState<string | null>(null);
  const [filterOptions, setFilterOptions] = useState<{ decades: number[]; regions: string[] }>({ decades: [], regions: [] });

  // A–Z letter filter
  const [letterFilter, setLetterFilter] = useState<string | null>(null);

  // Debounce search — clear letter filter when user types
  useEffect(() => {
    if (search) setLetterFilter(null);
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  // Load status banner
  useEffect(() => {
    libraryApi.getStatus().then(setStatus).catch(() => {});
  }, []);

  // Library is single-platform; default backend filter from environment/status.
  useEffect(() => {
    if (status?.platform) {
      setBackendFilter(status.platform);
    }
  }, [status?.platform]);

  // Load filter options once
  useEffect(() => {
    libraryBrowseApi.getArtistFilterOptions()
      .then(res => setFilterOptions({ decades: res.decades, regions: res.regions }))
      .catch(() => {});
  }, []);

  // Fetch data when tab / filters / search change
  const fetchArtists = useCallback(async (q: string, backend: string, decade: number | null, region: string | null, letter: string | null) => {
    setLoading(true);
    setFetchError(null);
    try {
      const res = await libraryBrowseApi.getArtists({
        q: q || undefined,
        backend: backend !== 'all' ? backend : undefined,
        decade: decade ?? undefined,
        region: region ?? undefined,
        letter: letter ?? undefined,
        per_page: 200,
      });
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
    if (tab === 'artists') fetchArtists(debouncedSearch, backendFilter, decadeFilter, regionFilter, letterFilter);
    else if (tab === 'albums') fetchAlbums(debouncedSearch, backendFilter, recordTypeFilter);
    else fetchTracks(debouncedSearch);
  }, [tab, debouncedSearch, backendFilter, recordTypeFilter, decadeFilter, regionFilter, letterFilter, fetchArtists, fetchAlbums, fetchTracks]);

  // Run pipeline (sync + enrich in one pass)
  const handleSync = useCallback(async () => {
    setSyncing(true);
    try {
      await enrichmentApi.runFull();
      const s = await libraryApi.getStatus();
      setStatus(s);
    } catch { /* ignore */ }
    finally { setSyncing(false); }
  }, []);

  const handleTabChange = (t: Tab) => {
    setTab(t);
    setSearch('');
    if (t === 'albums') {
      setRecordTypeFilter('all');
    }
  };

  const handlePlaylistsTab = () => {
    navigate({ to: '/library/playlists' });
  };

  const handleShufflePlayArtistCard = useCallback(async (artistItem: LibArtist) => {
    try {
      const res = await libraryBrowseApi.getTracks({ artist_id: artistItem.id, per_page: 500 });
      const queueTracks: PlayerTrack[] = res.tracks.map(t => ({
        id: t.id,
        title: t.title,
        artist: artistItem.name,
        album: t.album_title ?? '',
        duration: t.duration,
        thumb_url: t.thumb_url ?? null,
        thumb_hash: t.thumb_hash ?? null,
        source_platform: artistItem.source_platform ?? 'navidrome',
        codec: t.codec,
        bitrate: t.bitrate,
        bit_depth: t.bit_depth,
        sample_rate: t.sample_rate,
      }));
      if (!queueTracks.length) {
        toastError(`No playable tracks found for "${artistItem.name}"`);
        return;
      }
      const shuffled = [...queueTracks].sort(() => Math.random() - 0.5);
      playQueue(shuffled);
    } catch (err) {
      toastError(err instanceof Error ? err.message : 'Failed to shuffle play artist');
    }
  }, [playQueue, toastError]);

  const handleHoverPlayAlbumCard = useCallback(async (albumItem: LibAlbum) => {
    try {
      const res = await libraryBrowseApi.getAlbum(albumItem.id);
      const queueTracks: PlayerTrack[] = res.tracks.map(t => ({
        id: t.id,
        title: t.title,
        artist: albumItem.artist_name,
        album: albumItem.title,
        duration: t.duration,
        thumb_url: albumItem.thumb_url ?? t.thumb_url ?? null,
        thumb_hash: albumItem.thumb_hash ?? t.thumb_hash ?? null,
        source_platform: albumItem.source_platform ?? status?.platform ?? 'navidrome',
        codec: t.codec,
        bitrate: t.bitrate,
        bit_depth: t.bit_depth,
        sample_rate: t.sample_rate,
      }));
      if (!queueTracks.length) {
        toastError(`No playable tracks found for "${albumItem.title}"`);
        return;
      }
      playQueue(queueTracks);
    } catch (err) {
      toastError(err instanceof Error ? err.message : 'Failed to play album');
    }
  }, [playQueue, status?.platform, toastError]);

  const playLibraryTrackNow = useCallback((trackId: string) => {
    const queueTracks: PlayerTrack[] = tracks.map(t => ({
      id: t.id,
      title: t.title,
      artist: t.artist_name ?? '',
      album: t.album_title ?? '',
      duration: t.duration,
      thumb_url: t.thumb_url ?? null,
      thumb_hash: t.thumb_hash ?? null,
      source_platform: status?.platform ?? 'navidrome',
      codec: t.codec,
      bitrate: t.bitrate,
      bit_depth: t.bit_depth,
      sample_rate: t.sample_rate,
    }));
    const idx = queueTracks.findIndex(t => t.id === trackId);
    if (idx < 0 || queueTracks.length === 0) return;
    playQueue(queueTracks.slice(idx));
  }, [playQueue, status?.platform, tracks]);

  // Root view
  const footerText = loading ? null
    : tab === 'artists' ? `${totalArtists} artist${totalArtists !== 1 ? 's' : ''}`
    : tab === 'albums'  ? `${totalAlbums} album${totalAlbums !== 1 ? 's' : ''}`
    :                     `${totalTracks} track${totalTracks !== 1 ? 's' : ''}`;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Status banner */}
      {status && (
        <div className="px-6 py-2.5 bg-base border-b border-border-subtle flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-3">
            <LibraryIcon size={14} className="text-accent flex-shrink-0" />
            <div className="flex items-center gap-2 text-xs font-mono text-text-secondary">
              <span>{status.track_count?.toLocaleString()} tracks</span>
              <span className="text-text-faint">·</span>
              <span>{(status as unknown as { enrich_pct?: number }).enrich_pct ?? 0}% enriched</span>
              {status.last_synced && (
                <>
                  <span className="text-text-faint">·</span>
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
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-surface-skeleton hover:bg-surface-raised border border-border text-text-muted hover:text-text-primary rounded-sm transition-colors"
          >
            <RefreshCw size={11} className={syncing ? 'animate-spin' : ''} />
            {syncing ? 'Running…' : 'Run Now'}
          </button>
        </div>
      )}

      {/* Header */}
      <div className="px-6 pt-5 pb-3 border-b border-border-subtle flex-shrink-0">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h1 className="page-title">Library</h1>
            <p className="text-text-muted text-sm mt-0.5">Browse your music collection</p>
          </div>
        </div>

        {/* Tabs + filters */}
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex gap-1 bg-surface p-0.5 rounded-sm">
            {(['artists', 'albums', 'tracks'] as Tab[]).map(t => (
              <button
                key={t}
                onClick={() => handleTabChange(t)}
                className={`px-4 py-1.5 text-sm font-medium rounded-sm transition-colors capitalize ${
                  tab === t ? 'bg-accent text-white' : 'text-text-muted hover:text-text-secondary'
                }`}
              >
                {t}
              </button>
            ))}
            <button
              onClick={handlePlaylistsTab}
              className="px-4 py-1.5 text-sm font-medium rounded-sm transition-colors capitalize text-text-muted hover:text-text-secondary"
            >
              playlists
            </button>
          </div>

          <div className="flex items-center gap-1.5 flex-wrap">
            {tab === 'artists' && status?.platform && (
              <span className="bg-accent/10 border border-accent/20 text-accent text-xs font-mono rounded-sm px-2 py-1.5 capitalize">
                {status.platform}
              </span>
            )}

            {tab === 'albums' && (
              <select
                value={recordTypeFilter}
                onChange={e => setRecordTypeFilter(e.target.value)}
                className="bg-surface border border-border text-text-primary text-xs font-mono rounded-sm px-2 py-1.5 appearance-none cursor-pointer focus:outline-none focus:border-accent"
              >
                <option value="all">All Releases</option>
                <option value="album">Albums</option>
                <option value="ep">EPs</option>
                <option value="single">Singles</option>
                <option value="compile">Compilations</option>
              </select>
            )}

            {tab === 'artists' && filterOptions.decades.length >= 2 && (
              <select
                value={decadeFilter ?? ''}
                onChange={e => setDecadeFilter(e.target.value ? Number(e.target.value) : null)}
                className="bg-surface border border-border text-text-primary text-xs font-mono rounded-sm px-2 py-1.5 appearance-none cursor-pointer focus:outline-none focus:border-accent"
              >
                <option value="">All Eras</option>
                {filterOptions.decades.map(d => (
                  <option key={d} value={d}>{d}s</option>
                ))}
              </select>
            )}

            {tab === 'artists' && filterOptions.regions.length >= 2 && (
              <select
                value={regionFilter ?? ''}
                onChange={e => setRegionFilter(e.target.value || null)}
                className="bg-surface border border-border text-text-primary text-xs font-mono rounded-sm px-2 py-1.5 appearance-none cursor-pointer focus:outline-none focus:border-accent"
              >
                <option value="">All Regions</option>
                {filterOptions.regions.map(r => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            )}

            {tab === 'albums' && (
              <select
                value={backendFilter}
                onChange={e => setBackendFilter(e.target.value)}
                className="bg-surface border border-border text-text-primary text-xs font-mono rounded-sm px-2 py-1.5 appearance-none cursor-pointer focus:outline-none focus:border-accent"
              >
                <option value="all">All Sources</option>
                <option value="plex">Plex</option>
                <option value="navidrome">Navidrome</option>
                <option value="jellyfin">Jellyfin</option>
              </select>
            )}

            {tab !== 'tracks' && (
              <div className="flex border border-border rounded-sm overflow-hidden">
                <button
                  onClick={() => setViewMode('grid')}
                  className={`px-2 py-1.5 transition-colors ${viewMode === 'grid' ? 'bg-surface-overlay text-text-primary' : 'text-text-muted hover:text-text-secondary'}`}
                >
                  <Grid3X3 size={14} />
                </button>
                <button
                  onClick={() => setViewMode('list')}
                  className={`px-2 py-1.5 transition-colors ${viewMode === 'list' ? 'bg-surface-overlay text-text-primary' : 'text-text-muted hover:text-text-secondary'}`}
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
                className="bg-surface border border-border text-text-primary placeholder-text-muted rounded-sm pl-8 pr-3 py-1.5 text-xs font-mono w-44 focus:outline-none focus:border-accent transition-colors"
              />
            </div>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-h-0 overflow-y-auto flex">
        <div className="flex-1 min-w-0">
        {fetchError && (
          <div className="p-4">
            <ApiErrorBanner
              error={fetchError}
              onRetry={() => {
                if (tab === 'artists') fetchArtists(debouncedSearch, backendFilter, decadeFilter, regionFilter, letterFilter);
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
              {artists.map(a => (
                <ArtistCard
                  key={a.id}
                  artist={a}
                  viewMode="grid"
                  onShufflePlay={handleShufflePlayArtistCard}
                />
              ))}
            </div>
          ) : (
            <div className="py-2">
              {artists.map(a => (
                <ArtistCard
                  key={a.id}
                  artist={a}
                  viewMode="list"
                  onShufflePlay={handleShufflePlayArtistCard}
                />
              ))}
            </div>
          )
        )}

        {!loading && !fetchError && tab === 'albums' && (
          viewMode === 'grid' ? (
            <div className="p-4 grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-3">
              {albums.map(al => <AlbumCard key={al.id} album={al} viewMode="grid" onHoverPlay={handleHoverPlayAlbumCard} />)}
            </div>
          ) : (
            <div className="py-2">
              {albums.map(al => <AlbumCard key={al.id} album={al} viewMode="list" onHoverPlay={handleHoverPlayAlbumCard} />)}
            </div>
          )
        )}

        {!loading && !fetchError && tab === 'tracks' && (
          <div>
            <div className="grid grid-cols-[1.75rem_2rem_1fr_1fr_1fr_3.5rem_auto] gap-3 px-6 py-2 border-b border-surface sticky top-0 bg-base">
              <span />
              <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest text-right">#</span>
              <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest">Title</span>
              <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest">Artist</span>
              <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest">Album</span>
              <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest text-right">Dur</span>
              <span />
            </div>
            {tracks.map((t, i) => (
              <div
                key={t.id}
                onDoubleClick={() => playLibraryTrackNow(t.id)}
                className="group grid grid-cols-[1.75rem_2rem_1fr_1fr_1fr_3.5rem_auto] gap-3 items-center px-6 py-2 rounded-sm border border-transparent hover:bg-surface-raised hover:border-border-strong transition-colors"
              >
                <button
                  onClick={() => playLibraryTrackNow(t.id)}
                  className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-accent transition-all"
                  title="Play now"
                  aria-label={`Play ${t.title}`}
                >
                  <Play size={14} className="fill-current" />
                </button>
                <span className="font-mono text-xs text-text-muted tabular-nums text-right">{String(i + 1).padStart(3, '0')}</span>
                <span className="text-sm text-text-primary group-hover:text-accent truncate">{t.title}</span>
                <span className="font-mono text-xs text-text-secondary group-hover:text-accent/80 truncate">{t.artist_name}</span>
                <span className="font-mono text-xs text-text-muted group-hover:text-accent/80 truncate">{t.album_title}</span>
                <span className="font-mono text-xs text-text-muted group-hover:text-accent/80 tabular-nums text-right">{formatDuration(t.duration)}</span>
                <button
                  onClick={() => picker.openPicker(t)}
                  className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-accent transition-all"
                  title="Add to playlist"
                  aria-label={`Add ${t.title} to playlist`}
                >
                  <ListPlus size={14} />
                </button>
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
            {debouncedSearch
              ? `No ${tab} matching "${debouncedSearch}"`
              : letterFilter
                ? `No ${tab} starting with "${letterFilter}"`
                : `No ${tab} in library`}
          </p>
        )}
        </div>

        {/* A–Z filter rail — visible for artists tab when no search active */}
        {tab === 'artists' && !debouncedSearch && (
          <div className="w-7 flex-shrink-0 flex flex-col items-center py-2 sticky top-0 self-start">
            {AZ_LETTERS.map(letter => (
              <button
                key={letter}
                onClick={() => setLetterFilter(letterFilter === letter ? null : letter)}
                className={`font-mono text-[11px] leading-[18px] w-6 text-center rounded transition-colors ${
                  letterFilter === letter
                    ? 'text-accent bg-accent/10 font-bold'
                    : 'text-text-muted hover:text-text-secondary hover:bg-surface-raised'
                }`}
              >
                {letter}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Footer count */}
      {footerText && (
        <div className="px-6 py-2 border-t border-border-subtle flex-shrink-0">
          <span className="font-mono text-[10px] text-text-muted">{footerText}</span>
        </div>
      )}

      <PlaylistPickerModal {...picker} />

      {/* TODO Phase 14: PlayerBar */}
    </div>
  );
}
