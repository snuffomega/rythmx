import { useState, useEffect, useCallback } from 'react';
import {
  Loader2, Search, Grid3X3, List, RefreshCw,
  Library as LibraryIcon, ChevronLeft, ChevronRight, Star,
  ListPlus, MoreHorizontal, User, Disc, Play, X, Shuffle, Camera, Check,
} from 'lucide-react';
import { Link, useNavigate, useRouter } from '@tanstack/react-router';
import { libraryBrowseApi, libraryApi, enrichmentApi } from '../services/api';
import { useImage } from '../hooks/useImage';
import { getImageUrl } from '../utils/imageUrl';
import { usePlayerStore, type PlayerTrack } from '../stores/usePlayerStore';
import { ApiErrorBanner, AudioQualityBadge } from '../components/common';
import type { LibArtist, LibAlbum, LibTrack, LibArtistDetail as LibArtistDetailType, LibAlbumDetail as LibAlbumDetailType, LibraryStatus, MissingAlbum, MissingReleaseGroup, ReleaseDetail, ReleaseTrack, ReleaseSibling, UserReleasePrefs, SimilarArtist } from '../types';

type Tab = 'artists' | 'albums' | 'tracks';
type ViewMode = 'grid' | 'list';

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

type KindGroup<T> = { kind: string; label: string; items: T[] };

function groupByKind<T extends { kind?: string | null; record_type?: string | null }>(
  items: T[],
  kindField: 'kind' | 'record_type' = 'record_type'
): KindGroup<T>[] {
  const order = ['album', 'ep', 'single', 'compilation'];
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const k = (kindField === 'kind' ? item.kind : item.record_type)?.toLowerCase() || 'album';
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k)!.push(item);
  }
  return order
    .filter(k => groups.has(k))
    .map(k => ({
      kind: k,
      label: k === 'ep' ? 'EPs' : k.charAt(0).toUpperCase() + k.slice(1) + 's',
      items: groups.get(k)!,
    }));
}

function parseTags(json: string | null): string[] {
  try { return (JSON.parse(json ?? '[]') as string[]).filter(Boolean); }
  catch { return []; }
}

function mergeUniqueTags(a: string | null, b: string | null): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const t of [...parseTags(a), ...parseTags(b)]) {
    const k = t.toLowerCase();
    if (!seen.has(k)) { seen.add(k); result.push(t); }
  }
  return result;
}

function formatCount(n: number | null): string | null {
  if (n == null) return null;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString();
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

function ArtistImage({ name, size, imageUrl }: { name: string; size: number; imageUrl?: string | null }) {
  const [errored, setErrored] = useState(false);
  const hasDirectUrl = imageUrl && imageUrl.startsWith('http');
  const resolvedUrl = useImage('artist', name, '', !!hasDirectUrl);
  const src = hasDirectUrl ? imageUrl : resolvedUrl;
  if (src && !errored) return <img src={getImageUrl(src)} alt={name} className="w-full h-full object-cover" onError={() => setErrored(true)} />;
  return <User size={size} className="text-text-muted" />;
}

function AlbumImage({ title, artist, size, thumbUrl }: { title: string; artist: string; size: number; thumbUrl?: string | null }) {
  const [errored, setErrored] = useState(false);
  const hasDirectUrl = thumbUrl && thumbUrl.startsWith('http');
  const resolvedUrl = useImage('album', title, artist, !!hasDirectUrl);
  const src = hasDirectUrl ? thumbUrl : resolvedUrl;
  if (src && !errored) return <img src={getImageUrl(src)} alt={title} className="w-full h-full object-cover" onError={() => setErrored(true)} />;
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
  viewMode: ViewMode;
}

function ArtistCard({ artist, viewMode }: ArtistCardProps) {
  const genre = firstTag(artist.lastfm_tags_json);

  if (viewMode === 'grid') {
    return (
      <Link
        to="/library/artist/$id"
        params={{ id: artist.id }}
        className="text-left group hover:bg-[#111] rounded-sm p-2 transition-colors block"
      >
        <div className="aspect-square bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center mb-2 border border-[#222]">
          <ArtistImage name={artist.name} size={32} imageUrl={artist.image_url} />
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
      className="w-full flex items-center gap-3 px-2 py-2 hover:bg-[#111] transition-colors rounded-sm"
    >
      <div className="w-10 h-10 flex-shrink-0 bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center border border-[#222]">
        <ArtistImage name={artist.name} size={18} imageUrl={artist.image_url} />
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

// ---------------------------------------------------------------------------
// Album cards
// ---------------------------------------------------------------------------

interface AlbumCardProps {
  album: LibAlbum;
  viewMode: ViewMode;
}

function AlbumCard({ album, viewMode }: AlbumCardProps) {
  if (viewMode === 'grid') {
    return (
      <Link
        to="/library/album/$id"
        params={{ id: album.id }}
        className="text-left group hover:bg-[#111] rounded-sm p-2 transition-colors block"
      >
        <div className="aspect-square bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center mb-2 border border-[#222]">
          <AlbumImage title={album.title} artist={album.artist_name} size={32} thumbUrl={album.thumb_url} />
        </div>
        <p className="text-text-primary text-sm font-medium truncate">{album.title}</p>
        {album.year && <p className="text-text-muted text-xs font-mono">{album.year}</p>}
      </Link>
    );
  }

  return (
    <Link
      to="/library/album/$id"
      params={{ id: album.id }}
      className="w-full flex items-center gap-3 px-2 py-2 hover:bg-[#111] transition-colors rounded-sm"
    >
      <div className="w-10 h-10 flex-shrink-0 bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center border border-[#222]">
        <AlbumImage title={album.title} artist={album.artist_name} size={18} thumbUrl={album.thumb_url} />
      </div>
      <div className="flex-1 min-w-0 text-left">
        <p className="text-text-primary text-sm font-medium truncate">{album.title}</p>
        <p className="text-text-muted text-xs font-mono truncate">{album.artist_name}</p>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0 text-text-muted text-xs font-mono">
        {album.year && <span>{album.year}</span>}
        {album.record_type && <span className="capitalize">{album.record_type}</span>}
        <SourceChip backend={album.source_platform} />
      </div>
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Missing album card
// ---------------------------------------------------------------------------

function MissingAlbumCard({ release, onDismiss }: { release: MissingAlbum; onDismiss?: (id: string) => void }) {
  const inner = (
    <div className="text-left group relative rounded-sm p-2 opacity-70 hover:opacity-90 transition-opacity">
      {onDismiss && release.id && (
        <button
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); onDismiss(release.id!); }}
          className="absolute top-3 left-3 z-10 opacity-0 group-hover:opacity-100 bg-black/70 hover:bg-red-500/80 text-text-muted hover:text-white rounded-full p-0.5 transition-all"
          aria-label="Dismiss"
        >
          <X size={12} />
        </button>
      )}
      <div className="relative aspect-square bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center mb-2 border border-dashed border-[#333]">
        {release.thumb_url ? (
          <img src={getImageUrl(release.thumb_url)} alt={release.album_title}
               className="w-full h-full object-cover" loading="lazy" onError={(e) => { e.currentTarget.style.display = 'none'; }} />
        ) : (
          <Disc size={32} className="text-text-muted" />
        )}
        <span className="absolute top-1 right-1 bg-[#333] text-text-muted text-[9px] font-mono px-1.5 py-0.5 rounded-sm uppercase">
          Missing
        </span>
      </div>
      <p className="text-text-primary text-sm font-medium truncate">{release.display_title || release.album_title}</p>
      <div className="flex items-center gap-1.5">
        {release.release_date && (
          <span className="text-text-muted text-xs font-mono">{release.release_date.slice(0, 4)}</span>
        )}
        {release.version_type && release.version_type !== 'original' && (
          <span className="text-[9px] font-mono text-text-muted/70 px-1 py-0.5 bg-[#1a1a1a] rounded-sm">{release.version_type}</span>
        )}
      </div>
    </div>
  );
  if (release.id) {
    return <Link to="/library/release/$id" params={{ id: release.id }}>{inner}</Link>;
  }
  return inner;
}

function MissingKindGroup({ group, onDismiss }: { group: KindGroup<MissingAlbum & { record_type?: string | null }>; onDismiss?: (id: string) => void }) {
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

// ---------------------------------------------------------------------------
// Missing release group card (auto-collapse editions)
// ---------------------------------------------------------------------------

function MissingGroupCard({ group, onDismiss }: { group: MissingReleaseGroup; onDismiss?: (id: string) => void }) {
  const primary = group.primary;

  // Single edition — render as regular MissingAlbumCard
  if (group.edition_count === 1) {
    return <MissingAlbumCard release={primary} onDismiss={onDismiss} />;
  }

  // Multi-edition — click-through to primary release detail (no inline accordion)
  const inner = (
    <div className="text-left group relative rounded-sm p-2 opacity-70 hover:opacity-90 transition-opacity">
      <div className="relative aspect-square bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center mb-2 border border-dashed border-[#333]">
        {primary.thumb_url ? (
          <img src={getImageUrl(primary.thumb_url)} alt={primary.album_title}
               className="w-full h-full object-cover" loading="lazy" onError={(e) => { e.currentTarget.style.display = 'none'; }} />
        ) : (
          <Disc size={32} className="text-text-muted" />
        )}
        <span className="absolute top-1 right-1 bg-[#333] text-text-muted text-[9px] font-mono px-1.5 py-0.5 rounded-sm">
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

// ---------------------------------------------------------------------------
// Artist detail
// ---------------------------------------------------------------------------

interface ArtistDetailProps {
  artistId: string;
}

export function ArtistDetail({ artistId }: ArtistDetailProps) {
  const [data, setData] = useState<LibArtistDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showMissing, setShowMissing] = useState(false);
  const [dismissedCount, setDismissedCount] = useState(0);
  const [missingGroups, setMissingGroups] = useState<MissingReleaseGroup[]>([]);
  const [bioExpanded, setBioExpanded] = useState(false);
  const [similar, setSimilar] = useState<SimilarArtist[] | null>(null);
  const [coverEditing, setCoverEditing] = useState(false);
  const [coverInput, setCoverInput] = useState('');
  const [coverSaving, setCoverSaving] = useState(false);
  const [artistImageUrl, setArtistImageUrl] = useState<string | null>(null);
  const playQueue = usePlayerStore(s => s.playQueue);
  const enqueueNext = usePlayerStore(s => s.enqueueNext);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setSimilar(null);
    setBioExpanded(false);
    libraryBrowseApi.getArtist(artistId)
      .then(res => {
        setData({ artist: res.artist, albums: res.albums, top_tracks: res.top_tracks, missing_albums: res.missing_albums || [] });
        setDismissedCount(res.dismissed_count ?? 0);
        setMissingGroups(res.missing_groups ?? []);
        setArtistImageUrl(res.artist.image_url ?? null);
      })
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load artist'))
      .finally(() => setLoading(false));
  }, [artistId]);

  useEffect(() => {
    libraryBrowseApi.getSimilarArtists(artistId)
      .then(res => setSimilar(res.similar ?? []))
      .catch(() => setSimilar([]));
  }, [artistId]);

  const handleDismiss = useCallback((releaseId: string) => {
    libraryBrowseApi.updateReleasePrefs(releaseId, { dismissed: true }).then(() => {
      setData(prev => {
        if (!prev) return prev;
        return { ...prev, missing_albums: (prev.missing_albums || []).filter(r => r.id !== releaseId) };
      });
      // Also filter from groups — remove the edition, remove the group if empty
      setMissingGroups(prev => prev
        .map(g => ({
          ...g,
          editions: g.editions.filter(e => e.id !== releaseId),
          edition_count: g.editions.filter(e => e.id !== releaseId).length,
          primary: g.primary.id === releaseId && g.editions.length > 1
            ? g.editions.find(e => e.id !== releaseId)!
            : g.primary,
        }))
        .filter(g => g.editions.length > 0 && !g.editions.every(e => e.is_owned))
      );
      setDismissedCount(c => c + 1);
    });
  }, []);

  const fetchAllArtistTracks = useCallback(async (artistObj: LibArtist): Promise<PlayerTrack[]> => {
    const res = await libraryBrowseApi.getTracks({ artist_id: artistObj.id, per_page: 500 });
    return res.tracks.map(t => ({
      id: t.id,
      title: t.title,
      artist: artistObj.name,
      album: t.album_title ?? '',
      duration: t.duration,
      thumb_url: null,
      source_platform: artistObj.source_platform ?? 'navidrome',
      codec: t.codec,
      bitrate: t.bitrate,
      bit_depth: t.bit_depth,
      sample_rate: t.sample_rate,
    }));
  }, []);

  const handlePlayArtist = useCallback(async () => {
    if (!data) return;
    const tracks = await fetchAllArtistTracks(data.artist);
    if (tracks.length) playQueue(tracks);
  }, [data, fetchAllArtistTracks, playQueue]);

  const handleShuffleArtist = useCallback(async () => {
    if (!data) return;
    const tracks = await fetchAllArtistTracks(data.artist);
    if (!tracks.length) return;
    const shuffled = [...tracks].sort(() => Math.random() - 0.5);
    playQueue(shuffled);
  }, [data, fetchAllArtistTracks, playQueue]);

  const handlePlaySimilar = useCallback(async (similarArtist: SimilarArtist) => {
    if (!similarArtist.library_id) return;
    const res = await libraryBrowseApi.getTracks({ artist_id: similarArtist.library_id, per_page: 500 });
    const tracks: PlayerTrack[] = res.tracks.map(t => ({
      id: t.id,
      title: t.title,
      artist: similarArtist.name,
      album: t.album_title ?? '',
      duration: t.duration,
      thumb_url: null,
      source_platform: 'navidrome',
      codec: t.codec,
      bitrate: t.bitrate,
      bit_depth: t.bit_depth,
      sample_rate: t.sample_rate,
    }));
    if (tracks.length) enqueueNext(tracks);
  }, [enqueueNext]);

  const handleSaveCover = useCallback(async () => {
    if (!coverInput.trim() || !data) return;
    setCoverSaving(true);
    try {
      await libraryBrowseApi.setArtistCover(data.artist.id, coverInput.trim());
      setArtistImageUrl(coverInput.trim());
      setCoverEditing(false);
      setCoverInput('');
    } finally {
      setCoverSaving(false);
    }
  }, [coverInput, data]);

  if (loading) {
    return <div className="flex-1 flex items-center justify-center"><Loader2 size={20} className="animate-spin text-text-muted" /></div>;
  }
  if (error || !data) {
    return <ApiErrorBanner error={error ?? 'Not found'} onRetry={() => setLoading(true)} />;
  }

  const { artist, albums, top_tracks, missing_albums = [] } = data;
  const totalDuration = top_tracks.reduce((s, t) => s + (t.duration ?? 0), 0);
  const tags = mergeUniqueTags(artist.lastfm_tags_json, artist.genres_json).slice(0, 8);
  const listeners = formatCount(artist.listener_count);
  const fans = formatCount(artist.fans_deezer);
  const bioShort = artist.bio_lastfm ? artist.bio_lastfm.slice(0, 220) : null;
  const bioFull = artist.bio_lastfm ?? null;
  const bioTruncated = bioFull && bioFull.length > 220;
  const similarInLib = (similar ?? []).filter(s => s.in_library);
  const similarNotInLib = (similar ?? []).filter(s => !s.in_library);

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Breadcrumb */}
      <div className="px-8 pt-4 pb-2">
        <Link to="/library" className="flex items-center gap-1.5 text-text-muted hover:text-text-primary text-xs font-mono uppercase tracking-wider transition-colors">
          <LibraryIcon size={13} /> Library
        </Link>
      </div>

      {/* Hero */}
      <div className="px-8 pb-6 flex gap-8">
        {/* Artist image with cover edit overlay */}
        <div className="relative w-48 h-48 flex-shrink-0 group/cover">
          <div className="w-full h-full bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center border border-[#222]">
            <ArtistImage name={artist.name} size={56} imageUrl={artistImageUrl ?? artist.image_url} />
          </div>
          <button
            onClick={() => { setCoverEditing(v => !v); setCoverInput(''); }}
            className="absolute bottom-1.5 right-1.5 bg-black/70 hover:bg-black/90 text-text-muted hover:text-white rounded p-1 opacity-0 group-hover/cover:opacity-100 transition-all"
            aria-label="Edit cover photo"
          >
            <Camera size={13} />
          </button>
        </div>

        <div className="flex flex-col justify-center min-w-0 flex-1">
          <h1 className="text-3xl font-bold tracking-tighter text-text-primary mb-1">{artist.name}</h1>

          {/* Tag chips */}
          {tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {tags.map(tag => (
                <span key={tag} className="font-mono text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-[#1a1a1a] border border-[#2a2a2a] text-text-muted">
                  {tag}
                </span>
              ))}
            </div>
          )}

          {/* Formation badges */}
          {(artist.formed_year_musicbrainz || artist.area_musicbrainz || artist.begin_area_musicbrainz) && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {artist.formed_year_musicbrainz && (
                <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-[#1a1a1a] border border-[#2a2a2a] text-text-secondary">
                  est. {artist.formed_year_musicbrainz}
                </span>
              )}
              {(artist.begin_area_musicbrainz || artist.area_musicbrainz) && (
                <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-[#1a1a1a] border border-[#2a2a2a] text-text-secondary">
                  {artist.begin_area_musicbrainz ?? artist.area_musicbrainz}
                </span>
              )}
            </div>
          )}

          {/* Popularity signals */}
          {(listeners || fans) && (
            <p className="font-mono text-[10px] text-text-muted mb-2">
              {[listeners && `${listeners} listeners`, fans && `${fans} fans`].filter(Boolean).join(' · ')}
            </p>
          )}

          <div className="flex items-center gap-2 mt-1">
            <ConfidenceBadge value={artist.match_confidence} />
            <SourceChip backend={artist.source_platform} />
            <span className="font-mono text-[10px] text-text-muted">{artist.album_count} albums</span>
          </div>

          {/* Play / Shuffle buttons */}
          <div className="flex items-center gap-2 mt-3">
            <button
              onClick={handlePlayArtist}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-accent hover:bg-accent/80 rounded text-black text-xs font-semibold transition-colors"
              aria-label="Play artist"
            >
              <Play size={12} className="fill-current" /> Play
            </button>
            <button
              onClick={handleShuffleArtist}
              className="flex items-center gap-1.5 px-3 py-1.5 border border-[#333] hover:border-[#555] rounded text-text-secondary text-xs font-mono transition-colors"
              aria-label="Shuffle artist"
            >
              <Shuffle size={12} /> Shuffle
            </button>
          </div>

          {/* Cover URL editor */}
          {coverEditing && (
            <div className="flex items-center gap-2 mt-2">
              <input
                type="url"
                value={coverInput}
                onChange={e => setCoverInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') handleSaveCover(); if (e.key === 'Escape') { setCoverEditing(false); setCoverInput(''); } }}
                placeholder="Paste image URL…"
                className="flex-1 bg-[#111] border border-[#333] rounded px-2 py-1 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent font-mono"
                autoFocus
              />
              <button
                onClick={handleSaveCover}
                disabled={coverSaving || !coverInput.trim()}
                className="flex items-center gap-1 px-2 py-1 bg-accent hover:bg-accent/80 disabled:opacity-40 rounded text-black text-xs font-semibold transition-colors"
                aria-label="Save cover"
              >
                {coverSaving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
              </button>
              <button
                onClick={() => { setCoverEditing(false); setCoverInput(''); }}
                className="p-1 text-text-muted hover:text-text-primary transition-colors"
                aria-label="Cancel"
              >
                <X size={14} />
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-[#1a1a1a]" />

      {/* Bio */}
      {bioFull && (
        <div className="px-8 py-5">
          <h2 className="text-xs font-mono font-semibold text-text-muted uppercase tracking-widest mb-2">About</h2>
          <p className="text-sm text-text-secondary leading-relaxed whitespace-pre-line">
            {bioExpanded ? bioFull : (bioTruncated ? `${bioShort}…` : bioFull)}
          </p>
          {bioTruncated && (
            <button
              onClick={() => setBioExpanded(v => !v)}
              className="mt-1.5 font-mono text-[10px] text-accent hover:text-accent/80 transition-colors"
            >
              {bioExpanded ? 'Show less' : 'Show more'}
            </button>
          )}
        </div>
      )}

      {/* Similar Artists */}
      {similar !== null && (similarInLib.length > 0 || similarNotInLib.length > 0) && (
        <>
          <div className="border-t border-[#1a1a1a]" />
          <div className="px-8 py-5">
            <h2 className="text-xs font-mono font-semibold text-text-muted uppercase tracking-widest mb-3">Similar Artists</h2>

            {similarInLib.length > 0 && (
              <div className="mb-3">
                <p className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-1.5">In Your Library</p>
                <div className="flex flex-wrap gap-1.5">
                  {similarInLib.map(s => (
                    <div key={s.name} className="flex items-center gap-1">
                      <Link
                        to="/library/artist/$id"
                        params={{ id: s.library_id! }}
                        className="font-mono text-xs px-2 py-1 rounded bg-accent/10 border border-accent/20 text-accent hover:bg-accent/20 transition-colors"
                      >
                        {s.name}
                      </Link>
                      <button
                        onClick={() => handlePlaySimilar(s)}
                        className="p-1 text-text-muted hover:text-accent transition-colors"
                        aria-label={`Play ${s.name} radio`}
                        title="Add to queue"
                      >
                        <ListPlus size={13} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {similarNotInLib.length > 0 && (
              <div>
                <p className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-1.5">Also Try</p>
                <div className="flex flex-wrap gap-1.5">
                  {similarNotInLib.map(s => (
                    <span
                      key={s.name}
                      className="font-mono text-xs px-2 py-1 rounded bg-[#1a1a1a] border border-[#2a2a2a] text-text-muted"
                    >
                      {s.name}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </>
      )}

      <div className="border-t border-[#1a1a1a]" />

      {/* Top Tracks */}
      {top_tracks.length > 0 && (
        <div className="px-8 py-5">
          <h2 className="text-xs font-mono font-semibold text-text-muted uppercase tracking-widest mb-3">Popular Tracks</h2>
          <div>
            {top_tracks.map((t, i) => (
              <div key={t.id} className="group grid grid-cols-[2rem_1fr_1fr_3.5rem_auto] gap-3 items-center py-2 px-2 hover:bg-[#111] rounded-sm transition-colors">
                <span className="font-mono text-xs text-text-muted tabular-nums text-right">{i + 1}</span>
                <span className="text-sm text-text-primary truncate">{t.title}</span>
                <span className="font-mono text-xs text-text-muted truncate">{t.album_title}</span>
                <span className="font-mono text-xs text-text-muted tabular-nums text-right">{formatDuration(t.duration)}</span>
                <button
                  onClick={() => playQueue(top_tracks.map((tr, idx) => ({
                    id: tr.id,
                    title: tr.title,
                    artist: data.artist.name,
                    album: tr.album_title ?? '',
                    duration: tr.duration,
                    thumb_url: null,
                    source_platform: data.artist.source_platform ?? 'navidrome',
                    codec: tr.codec, bitrate: tr.bitrate,
                    bit_depth: tr.bit_depth, sample_rate: tr.sample_rate,
                  })).slice(i))}
                  className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-accent transition-all"
                  aria-label="Play"
                >
                  <Play size={14} />
                </button>
              </div>
            ))}
          </div>
          {totalDuration > 0 && (
            <p className="font-mono text-[10px] text-text-muted mt-2">{Math.round(totalDuration / 60000)} min total</p>
          )}
        </div>
      )}

      <div className="border-t border-[#1a1a1a]" />

      {/* Owned Releases — grouped by kind */}
      <div className="px-8 py-5">
        <h2 className="text-xs font-mono font-semibold text-text-muted uppercase tracking-widest mb-3">
          {albums.length} {albums.length === 1 ? 'Release' : 'Releases'}
        </h2>
        {groupByKind(albums, 'record_type').map(group => (
          <div key={group.kind} className="mb-5">
            <h3 className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-2">
              {group.label} ({group.items.length})
            </h3>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
              {group.items.map(al => <AlbumCard key={al.id} album={al} viewMode="grid" />)}
            </div>
          </div>
        ))}
      </div>

      {/* Missing Releases — collapsed by default */}
      {(missing_albums.length > 0 || missingGroups.length > 0) && (
        <>
          <div className="border-t border-[#1a1a1a]" />
          <div className="px-8 py-5">
            <button
              onClick={() => setShowMissing(!showMissing)}
              className="flex items-center gap-2 text-xs font-mono font-semibold text-text-muted uppercase tracking-widest mb-3 hover:text-text-secondary transition-colors"
            >
              <ChevronRight size={14} className={`transition-transform ${showMissing ? 'rotate-90' : ''}`} />
              {missingGroups.length > 0
                ? `${missingGroups.length} Missing ${missingGroups.length === 1 ? 'Release' : 'Releases'}`
                : `${missing_albums.length} Missing ${missing_albums.length === 1 ? 'Release' : 'Releases'}`
              }
              {dismissedCount > 0 && (
                <span className="text-[9px] font-normal normal-case tracking-normal text-text-muted ml-1">({dismissedCount} dismissed)</span>
              )}
            </button>
            {showMissing && (
              missingGroups.length > 0
                ? groupByKind(missingGroups, 'kind').map(kindGroup => (
                    <div key={kindGroup.kind} className="mb-4 ml-5">
                      <h3 className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-2">
                        {kindGroup.label} ({kindGroup.items.length})
                      </h3>
                      <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
                        {kindGroup.items.map(g => (
                          <MissingGroupCard key={g.canonical_release_id} group={g} onDismiss={handleDismiss} />
                        ))}
                      </div>
                    </div>
                  ))
                : groupByKind(missing_albums as (MissingAlbum & { record_type?: string | null })[], 'kind').map(group => (
                    <MissingKindGroup key={group.kind} group={group} onDismiss={handleDismiss} />
                  ))
            )}
          </div>
        </>
      )}

      <div className="h-4" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Album detail
// ---------------------------------------------------------------------------

interface AlbumDetailProps {
  albumId: string;
}

export function AlbumDetail({ albumId }: AlbumDetailProps) {
  const [data, setData] = useState<LibAlbumDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ratings, setRatings] = useState<Record<string, number>>({});
  const router = useRouter();
  const playQueue = usePlayerStore(s => s.playQueue);
  const enqueueNext = usePlayerStore(s => s.enqueueNext);

  function tracksToQueue(tList: typeof tracks, albumObj: typeof album): PlayerTrack[] {
    return tList.map(t => ({
      id: t.id,
      title: t.title,
      artist: albumObj?.artist_name ?? '',
      album: albumObj?.title ?? '',
      duration: t.duration,
      thumb_url: albumObj?.thumb_url ?? null,
      source_platform: albumObj?.source_platform ?? 'navidrome',
      codec: t.codec,
      bitrate: t.bitrate,
      bit_depth: t.bit_depth,
      sample_rate: t.sample_rate,
    }));
  }

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
        <Link to="/library" className="flex items-center gap-1.5 text-text-muted hover:text-text-primary text-xs font-mono uppercase tracking-wider transition-colors">
          <LibraryIcon size={13} /> Library
        </Link>
        <span className="text-[#333]">/</span>
        <button onClick={() => router.history.back()} className="flex items-center gap-1.5 text-text-muted hover:text-text-primary text-xs font-mono uppercase tracking-wider transition-colors">
          <ChevronLeft size={13} /> Back
        </button>
      </div>

      {/* Hero */}
      <div className="px-8 pb-6 flex gap-8">
        <div className="w-48 h-48 flex-shrink-0 bg-[#1a1a1a] rounded-sm overflow-hidden flex items-center justify-center border border-[#222]">
          <AlbumImage title={album.title} artist={album.artist_name} size={56} thumbUrl={album.thumb_url} />
        </div>
        <div className="flex flex-col justify-center min-w-0 flex-1">
          <Link
            to="/library/artist/$id"
            params={{ id: album.artist_id }}
            className="font-mono text-base text-accent hover:text-accent/80 transition-colors text-left w-fit mb-1"
          >
            {album.artist_name}
          </Link>
          <h1 className="text-3xl font-bold tracking-tighter text-text-primary mb-1">{album.title}</h1>
          <div className="flex items-center gap-3 text-xs font-mono text-text-muted mt-1">
            {album.year && <span>{album.year}</span>}
            {album.record_type && <><span className="text-[#333]">|</span><span className="capitalize">{album.record_type}</span></>}
            <span className="text-[#333]">|</span>
            <span>{tracks.length} tracks</span>
            {totalMin > 0 && <><span className="text-[#333]">|</span><span>{totalMin} min</span></>}
          </div>
          <div className="flex items-center gap-2 mt-3">
            <button
              onClick={() => playQueue(tracksToQueue(tracks, album))}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-accent hover:bg-accent/80 rounded text-black text-xs font-semibold transition-colors"
              aria-label="Play album"
            >
              <Play size={12} className="fill-current" /> Play
            </button>
            <button
              onClick={() => enqueueNext(tracksToQueue(tracks, album))}
              className="flex items-center gap-1.5 px-3 py-1.5 border border-[#333] hover:border-[#555] rounded text-text-secondary text-xs font-mono transition-colors"
              aria-label="Add to queue"
            >
              <ListPlus size={12} /> Queue
            </button>
          </div>
          <div className="flex items-center gap-2 mt-2">
            <ConfidenceBadge value={album.match_confidence} />
            <SourceChip backend={album.source_platform} />
            {album.needs_verification === 1 && (
              <span className="font-mono text-[10px] text-yellow-400 border border-yellow-400/30 px-1.5 py-0.5 rounded">verify</span>
            )}
          </div>
        </div>
      </div>

      <div className="border-t border-[#1a1a1a]" />

      {/* Track list */}
      <div className="px-8 py-5">
        <div className="grid grid-cols-[2rem_1fr_6rem_3.5rem_auto_auto_auto] gap-3 items-center px-2 py-1.5 border-b border-[#1a1a1a] mb-1">
          <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest text-right">#</span>
          <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest">Title</span>
          <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest">Rating</span>
          <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest text-right">Dur</span>
          <span />
          <span />
          <span />
        </div>
        {tracks.map(t => (
          <div key={t.id} className="group grid grid-cols-[2rem_1fr_6rem_3.5rem_auto_auto_auto_auto] gap-3 items-center px-2 py-2 hover:bg-[#111] rounded-sm transition-colors">
            <span className="font-mono text-xs text-text-muted tabular-nums text-right">
              {String(t.track_number ?? 0).padStart(2, '0')}
            </span>
            <span className="text-sm text-text-primary truncate">{t.title}</span>
            <StarRating value={ratings[t.id] ?? 0} onChange={v => handleRate(t.id, v)} size={12} />
            <span className="font-mono text-xs text-text-muted tabular-nums text-right">{formatDuration(t.duration)}</span>
            <AudioQualityBadge bit_depth={t.bit_depth} sample_rate={t.sample_rate} codec={t.codec} bitrate={t.bitrate} />
            <button
              onClick={() => {
                const q = tracksToQueue(tracks, album);
                const idx = q.findIndex(p => p.id === t.id);
                if (idx >= 0) { playQueue(q); usePlayerStore.getState().playAt(idx); }
              }}
              className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-accent transition-all"
              aria-label="Play"
            >
              <Play size={14} />
            </button>
            <button
              onClick={() => enqueueNext([tracksToQueue(tracks, album).find(p => p.id === t.id)!])}
              className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-accent transition-all"
              title="Add to queue"
            >
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

const AZ_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ#'.split('');


export function LibraryRoot() {
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
  };

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
            {syncing ? 'Running…' : 'Run Now'}
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

            {tab === 'artists' && filterOptions.decades.length >= 2 && (
              <select
                value={decadeFilter ?? ''}
                onChange={e => setDecadeFilter(e.target.value ? Number(e.target.value) : null)}
                className="bg-[#111] border border-[#222] text-text-primary text-xs font-mono rounded-sm px-2 py-1.5 appearance-none cursor-pointer focus:outline-none focus:border-accent"
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
                className="bg-[#111] border border-[#222] text-text-primary text-xs font-mono rounded-sm px-2 py-1.5 appearance-none cursor-pointer focus:outline-none focus:border-accent"
              >
                <option value="">All Regions</option>
                {filterOptions.regions.map(r => (
                  <option key={r} value={r}>{r}</option>
                ))}
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
              {artists.map(a => <ArtistCard key={a.id} artist={a} viewMode="grid" />)}
            </div>
          ) : (
            <div className="py-2">
              {artists.map(a => <ArtistCard key={a.id} artist={a} viewMode="list" />)}
            </div>
          )
        )}

        {!loading && !fetchError && tab === 'albums' && (
          viewMode === 'grid' ? (
            <div className="p-4 grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-3">
              {albums.map(al => <AlbumCard key={al.id} album={al} viewMode="grid" />)}
            </div>
          ) : (
            <div className="py-2">
              {albums.map(al => <AlbumCard key={al.id} album={al} viewMode="list" />)}
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
                    : 'text-text-muted hover:text-text-secondary hover:bg-[#1a1a1a]'
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
        <div className="px-6 py-2 border-t border-[#1a1a1a] flex-shrink-0">
          <span className="font-mono text-[10px] text-text-muted">{footerText}</span>
        </div>
      )}

      {/* TODO Phase 14: PlayerBar */}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Release detail (missing release click-through)
// ---------------------------------------------------------------------------

interface ReleaseDetailViewProps {
  releaseId: string;
}

export function ReleaseDetailView({ releaseId }: ReleaseDetailViewProps) {
  const [release, setRelease] = useState<ReleaseDetail | null>(null);
  const [tracks, setTracks] = useState<ReleaseTrack[]>([]);
  const [siblings, setSiblings] = useState<ReleaseSibling[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [prefs, setPrefs] = useState<UserReleasePrefs | null>(null);
  const [prefsLoading, setPrefsLoading] = useState(false);
  const [notes, setNotes] = useState('');

  useEffect(() => {
    setLoading(true);
    setError(null);
    libraryBrowseApi.getRelease(releaseId)
      .then(res => {
        setRelease(res.release);
        setTracks(res.tracks || []);
        setSiblings(res.siblings || []);
      })
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load release'))
      .finally(() => setLoading(false));
    libraryBrowseApi.getReleasePrefs(releaseId).then(res => {
      setPrefs(res.prefs);
      setNotes(res.prefs?.notes || '');
    });
  }, [releaseId]);

  const updatePref = useCallback((patch: { dismissed?: boolean; priority?: number; notes?: string }) => {
    setPrefsLoading(true);
    libraryBrowseApi.updateReleasePrefs(releaseId, patch)
      .then(() => libraryBrowseApi.getReleasePrefs(releaseId))
      .then(res => { setPrefs(res.prefs); if (res.prefs?.notes != null) setNotes(res.prefs.notes); })
      .finally(() => setPrefsLoading(false));
  }, [releaseId]);

  if (loading) {
    return <div className="flex-1 flex items-center justify-center"><Loader2 size={20} className="animate-spin text-text-muted" /></div>;
  }
  if (error || !release) {
    return <ApiErrorBanner error={error ?? 'Not found'} onRetry={() => setLoading(true)} />;
  }

  return (
    <div className="flex-1 overflow-y-auto custom-scrollbar">
      {/* Breadcrumb */}
      <div className="px-8 pt-4 pb-2 flex items-center gap-1.5">
        <Link to="/library" className="flex items-center gap-1 text-text-muted hover:text-text-primary text-xs font-mono uppercase tracking-wider transition-colors">
          <LibraryIcon size={13} /> Library
        </Link>
        {release && (
          <>
            <ChevronRight size={12} className="text-text-muted" />
            <Link to="/library/artist/$id" params={{ id: release.artist_id }}
                  className="text-text-muted hover:text-text-primary text-xs font-mono uppercase tracking-wider transition-colors truncate max-w-[200px]">
              {release.artist_name}
            </Link>
          </>
        )}
      </div>

      {/* Release header */}
      <div className="flex gap-6 px-8 py-5">
        <div className="w-48 h-48 flex-shrink-0 rounded-sm overflow-hidden bg-[#1a1a1a] border border-dashed border-[#333] flex items-center justify-center">
          {release.thumb_url ? (
            <img src={getImageUrl(release.thumb_url)} alt={release.title} className="w-full h-full object-cover" onError={(e) => { e.currentTarget.style.display = 'none'; }} />
          ) : (
            <Disc size={48} className="text-text-muted" />
          )}
        </div>
        <div className="flex flex-col justify-end">
          <p className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-1">
            {release.kind}{release.version_type && release.version_type !== 'original' && ` · ${release.version_type}`}
            <span className="ml-2 px-1.5 py-0.5 bg-[#333] rounded-sm text-[9px]">Missing</span>
          </p>
          <h1 className="text-2xl font-bold text-text-primary mb-1">{release.title}</h1>
          <p className="text-sm text-text-secondary font-mono">{release.artist_name}</p>
          <div className="flex gap-3 mt-2 text-[10px] font-mono text-text-muted">
            {release.release_date && <span>{release.release_date}</span>}
            {release.track_count != null && <span>{release.track_count} tracks</span>}
            {release.label && <span>{release.label}</span>}
            {release.genre_itunes && <span>{release.genre_itunes}</span>}
          </div>
          <div className="flex gap-2 mt-2">
            {release.catalog_source && (
              <span className="text-[9px] font-mono px-1.5 py-0.5 bg-[#222] text-text-muted rounded-sm uppercase">{release.catalog_source}</span>
            )}
            {release.explicit === 1 && (
              <span className="text-[9px] font-mono px-1.5 py-0.5 bg-[#333] text-text-muted rounded-sm">E</span>
            )}
          </div>
          {/* Edition switcher */}
          {siblings.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-3">
              <span className="text-[10px] font-mono px-2 py-1 bg-accent/20 text-accent rounded-sm border border-accent/30">
                {release.version_type || 'original'}
              </span>
              {siblings.map(sib => (
                <Link
                  key={sib.id}
                  to="/library/release/$id"
                  params={{ id: sib.id }}
                  className={`text-[10px] font-mono px-2 py-1 rounded-sm border transition-colors ${
                    sib.is_owned
                      ? 'bg-green-500/10 text-green-400 border-green-500/30 hover:bg-green-500/20'
                      : 'bg-[#1a1a1a] text-text-muted border-[#333] hover:border-[#555] hover:text-text-secondary'
                  }`}
                >
                  {sib.version_type || 'original'}
                  {sib.is_owned ? ' ✓' : ''}
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* User controls */}
      <div className="px-8 py-4 border-t border-[#1a1a1a] flex flex-wrap items-center gap-4">
        <button
          onClick={() => updatePref({ dismissed: !prefs?.dismissed })}
          disabled={prefsLoading}
          className={`text-xs font-mono px-3 py-1.5 rounded-sm border transition-colors ${
            prefs?.dismissed
              ? 'border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20'
              : 'border-[#333] bg-[#1a1a1a] text-text-muted hover:text-text-secondary hover:border-[#444]'
          }`}
        >
          {prefs?.dismissed ? 'Dismissed — Undo' : 'Dismiss'}
        </button>
        <label className="flex items-center gap-2 text-xs font-mono text-text-muted">
          Priority
          <select
            value={prefs?.priority ?? 0}
            onChange={e => updatePref({ priority: Number(e.target.value) })}
            disabled={prefsLoading}
            className="bg-[#1a1a1a] border border-[#333] text-text-secondary text-xs font-mono px-2 py-1 rounded-sm"
          >
            <option value={0}>None</option>
            <option value={1}>Low</option>
            <option value={2}>Medium</option>
            <option value={3}>High</option>
          </select>
        </label>
        <div className="flex-1 min-w-[200px]">
          <input
            type="text"
            placeholder="Add a note..."
            value={notes}
            onChange={e => setNotes(e.target.value)}
            onBlur={() => { if (notes !== (prefs?.notes || '')) updatePref({ notes: notes || '' }); }}
            disabled={prefsLoading}
            className="w-full bg-[#1a1a1a] border border-[#333] text-text-secondary text-xs font-mono px-3 py-1.5 rounded-sm placeholder:text-text-muted/50 focus:outline-none focus:border-[#444]"
          />
        </div>
      </div>

      {/* Track listing */}
      {tracks.length > 0 && (
        <div className="px-8 py-4 border-t border-[#1a1a1a]">
          <h2 className="text-xs font-mono font-semibold text-text-muted uppercase tracking-widest mb-3">
            Track Listing
          </h2>
          <div className="space-y-0.5">
            {tracks.map((t, i) => (
              <div key={i} className="flex items-center gap-3 py-1.5 px-2 rounded-sm hover:bg-[#1a1a1a] transition-colors">
                <span className="text-text-muted font-mono text-xs w-6 text-right flex-shrink-0">{t.track_number}</span>
                <span className="text-text-primary text-sm flex-1 truncate">{t.title}</span>
                <span className="text-text-muted font-mono text-xs flex-shrink-0">{formatDuration(t.duration_ms)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {tracks.length === 0 && !loading && (
        <div className="px-8 py-4 border-t border-[#1a1a1a]">
          <p className="text-text-muted text-xs font-mono">No track listing available</p>
        </div>
      )}

      <div className="h-4" />
    </div>
  );
}
