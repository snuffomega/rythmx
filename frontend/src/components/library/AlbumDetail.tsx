import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Loader2, Library as LibraryIcon, ChevronLeft,
  ListPlus, MoreHorizontal, Disc, Play, X, Shuffle, Camera, Check,
} from 'lucide-react';
import { Link, useRouter } from '@tanstack/react-router';
import { libraryBrowseApi } from '../../services/api';
import { usePlayerStore, type PlayerTrack } from '../../stores/usePlayerStore';
import { useToastStore } from '../../stores/useToastStore';
import { usePlaylistPicker } from '../../hooks/usePlaylistPicker';
import { ApiErrorBanner, AudioQualityBadge, PlaylistPickerModal } from '../common';
import { AlbumImage } from './primitives/AlbumImage';
import { ConfidenceBadge } from './primitives/ConfidenceBadge';
import { SourceChip } from './primitives/SourceChip';
import { StarRating } from './primitives/StarRating';
import { formatDuration } from './utils';
import { getImageUrl } from '../../utils/imageUrl';
import type {
  LibAlbumDetail as LibAlbumDetailType,
  LibTrack,
  LibAlbum,
  AuditCandidateAlbum,
  AuditCandidateItem,
} from '../../types';

interface AlbumDetailProps {
  albumId: string;
}

export function AlbumDetail({ albumId }: AlbumDetailProps) {
  const toastSuccess = useToastStore(s => s.success);
  const toastError = useToastStore(s => s.error);
  const [data, setData] = useState<LibAlbumDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ratings, setRatings] = useState<Record<string, number>>({});
  const [coverEditing, setCoverEditing] = useState(false);
  const [coverInput, setCoverInput] = useState('');
  const [coverSaving, setCoverSaving] = useState(false);
  const [albumImageUrl, setAlbumImageUrl] = useState<string | null>(null);
  const picker = usePlaylistPicker();
  const [openTrackMenuId, setOpenTrackMenuId] = useState<string | null>(null);
  const [fixMatchOpen, setFixMatchOpen] = useState(false);
  const [fixMatchLoading, setFixMatchLoading] = useState(false);
  const [fixMatchSaving, setFixMatchSaving] = useState(false);
  const [fixMatchError, setFixMatchError] = useState<string | null>(null);
  const [fixMatchAlbum, setFixMatchAlbum] = useState<AuditCandidateAlbum | null>(null);
  const [fixMatchCandidates, setFixMatchCandidates] = useState<Record<'itunes' | 'deezer', AuditCandidateItem[]>>({
    itunes: [],
    deezer: [],
  });
  const router = useRouter();
  const playQueue = usePlayerStore(s => s.playQueue);
  const enqueueNext = usePlayerStore(s => s.enqueueNext);
  const addToQueue = usePlayerStore(s => s.addToQueue);

  function tracksToQueue(tList: LibTrack[], albumObj: LibAlbum | null): PlayerTrack[] {
    return tList.map(t => ({
      id: t.id,
      title: t.title,
      artist: albumObj?.artist_name ?? '',
      album: albumObj?.title ?? '',
      duration: t.duration,
      thumb_url: albumObj?.thumb_url ?? null,
      thumb_hash: albumObj?.thumb_hash ?? null,
      source_platform: albumObj?.source_platform ?? 'navidrome',
      codec: t.codec,
      bitrate: t.bitrate,
      bit_depth: t.bit_depth,
      sample_rate: t.sample_rate,
    }));
  }

  const loadAlbumDetail = useCallback(async (opts?: { preserveLoading?: boolean }) => {
    if (!opts?.preserveLoading) {
      setLoading(true);
      setError(null);
    }
    try {
      const res = await libraryBrowseApi.getAlbum(albumId);
      setData({ album: res.album, tracks: res.tracks });
      const init: Record<string, number> = {};
      res.tracks.forEach(t => { init[t.id] = t.rating; });
      setRatings(init);
      setAlbumImageUrl(res.album.thumb_url ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load album');
    } finally {
      if (!opts?.preserveLoading) {
        setLoading(false);
      }
    }
  }, [albumId]);

  useEffect(() => {
    loadAlbumDetail();
  }, [loadAlbumDetail]);

  const handleRate = useCallback(async (trackId: string, rating: number) => {
    setRatings(prev => ({ ...prev, [trackId]: rating }));
    try { await libraryBrowseApi.rateTrack(trackId, rating); } catch { /* optimistic - silently ignore */ }
  }, []);

  const handleSaveCover = useCallback(async () => {
    const nextUrl = coverInput.trim();
    if (!nextUrl || !data) return;
    setCoverSaving(true);
    try {
      await libraryBrowseApi.setAlbumCover(data.album.id, nextUrl);
      setAlbumImageUrl(nextUrl);
      setCoverEditing(false);
      setCoverInput('');
      toastSuccess('Album cover updated');
    } catch (err) {
      toastError(err instanceof Error ? err.message : 'Failed to update album cover');
    } finally {
      setCoverSaving(false);
    }
  }, [coverInput, data, toastSuccess, toastError]);

  const loadFixMatchCandidates = useCallback(async () => {
    setFixMatchLoading(true);
    setFixMatchError(null);
    try {
      const [itunesRes, deezerRes] = await Promise.all([
        libraryBrowseApi.getAuditCandidates({ album_id: albumId, source: 'itunes', limit: 20 }),
        libraryBrowseApi.getAuditCandidates({ album_id: albumId, source: 'deezer', limit: 20 }),
      ]);
      setFixMatchAlbum(itunesRes.album);
      setFixMatchCandidates({
        itunes: itunesRes.candidates,
        deezer: deezerRes.candidates,
      });
    } catch (err) {
      setFixMatchError(err instanceof Error ? err.message : 'Failed to load match candidates');
      setFixMatchCandidates({ itunes: [], deezer: [] });
    } finally {
      setFixMatchLoading(false);
    }
  }, [albumId]);

  const openFixMatchModal = useCallback(() => {
    setFixMatchOpen(true);
    setOpenTrackMenuId(null);
    void loadFixMatchCandidates();
  }, [loadFixMatchCandidates]);

  const closeFixMatchModal = useCallback(() => {
    setFixMatchOpen(false);
    setFixMatchError(null);
  }, []);

  const confirmFixMatchCandidate = useCallback(async (source: 'itunes' | 'deezer', confirmedId: string) => {
    const trimmed = confirmedId.trim();
    if (!trimmed) return;
    setFixMatchSaving(true);
    setFixMatchError(null);
    try {
      await libraryBrowseApi.confirmAuditItem({
        entity_type: 'album',
        entity_id: albumId,
        source,
        confirmed_id: trimmed,
      });
      toastSuccess(`Saved manual ${source === 'itunes' ? 'iTunes' : 'Deezer'} match`);
      await Promise.all([
        loadAlbumDetail({ preserveLoading: true }),
        loadFixMatchCandidates(),
      ]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to confirm match';
      setFixMatchError(msg);
      toastError(msg);
    } finally {
      setFixMatchSaving(false);
    }
  }, [albumId, loadAlbumDetail, loadFixMatchCandidates, toastError, toastSuccess]);

  const rejectFixMatchSource = useCallback(async (source: 'itunes' | 'deezer') => {
    setFixMatchSaving(true);
    setFixMatchError(null);
    try {
      await libraryBrowseApi.rejectAuditItem({
        entity_type: 'album',
        entity_id: albumId,
        source,
      });
      toastSuccess(`Cleared ${source === 'itunes' ? 'iTunes' : 'Deezer'} match`);
      await Promise.all([
        loadAlbumDetail({ preserveLoading: true }),
        loadFixMatchCandidates(),
      ]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to reject match';
      setFixMatchError(msg);
      toastError(msg);
    } finally {
      setFixMatchSaving(false);
    }
  }, [albumId, loadAlbumDetail, loadFixMatchCandidates, toastError, toastSuccess]);

  useEffect(() => {
    if (!openTrackMenuId) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target?.closest('[data-track-menu-root="true"]')) {
        setOpenTrackMenuId(null);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpenTrackMenuId(null);
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [openTrackMenuId]);

  const album = data?.album ?? null;
  const tracks = data?.tracks ?? [];
  const albumQueue = useMemo(() => {
    if (!album) return [];
    return tracksToQueue(tracks, album);
  }, [tracks, album]);
  const playTrackNow = (trackId: string) => {
    const idx = albumQueue.findIndex(p => p.id === trackId);
    if (idx >= 0) {
      playQueue(albumQueue);
      usePlayerStore.getState().playAt(idx);
    }
  };

  if (loading) {
    return <div className="flex-1 flex items-center justify-center"><Loader2 size={20} className="animate-spin text-text-muted" /></div>;
  }
  if (error || !album || !data) {
    return <ApiErrorBanner error={error ?? 'Not found'} onRetry={() => setLoading(true)} />;
  }

  const totalMs = tracks.reduce((s, t) => s + (t.duration ?? 0), 0);
  const totalMin = Math.round(totalMs / 60000);
  const fixMatchSourceLabel: Record<'itunes' | 'deezer', string> = {
    itunes: 'iTunes',
    deezer: 'Deezer',
  };
  const fixMatchContext = fixMatchAlbum ?? {
    id: album.id,
    artist_id: album.artist_id,
    artist_name: album.artist_name,
    title: album.title,
    itunes_album_id: null,
    deezer_id: null,
    match_confidence: album.match_confidence,
    needs_verification: album.needs_verification === 1,
    track_count: tracks.length,
  };

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Breadcrumb */}
      <div className="px-8 pt-4 pb-2 flex items-center gap-3">
        <Link to="/library" className="flex items-center gap-1.5 text-text-muted hover:text-text-primary text-xs font-mono uppercase tracking-wider transition-colors">
          <LibraryIcon size={13} /> Library
        </Link>
        <span className="text-text-faint">/</span>
        <button onClick={() => router.history.back()} className="flex items-center gap-1.5 text-text-muted hover:text-text-primary text-xs font-mono uppercase tracking-wider transition-colors">
          <ChevronLeft size={13} /> Back
        </button>
      </div>

      {/* Hero */}
      <div className="px-8 pb-6 flex gap-8">
        <div className="relative w-60 h-60 flex-shrink-0 group/cover">
          <div className="w-full h-full bg-surface-raised rounded-sm overflow-hidden flex items-center justify-center border border-border">
            <AlbumImage
              title={album.title}
              artist={album.artist_name}
              size={56}
              thumbUrl={albumImageUrl ?? album.thumb_url}
              thumbHash={album.thumb_hash}
              matchConfidence={album.match_confidence}
            />
          </div>
          <button
            onClick={() => { setCoverEditing(v => !v); setCoverInput(''); }}
            className="absolute bottom-1.5 right-1.5 bg-black/70 hover:bg-black/90 text-text-muted hover:text-white rounded p-1 opacity-0 group-hover/cover:opacity-100 transition-all"
            aria-label="Edit album cover"
          >
            <Camera size={13} />
          </button>
        </div>
        <div className="flex flex-col justify-center min-w-0 flex-1">
          <Link
            to="/library/artist/$id"
            params={{ id: album.artist_id }}
            className="font-mono text-xl text-accent hover:text-accent/80 transition-colors text-left w-fit mb-1"
          >
            {album.artist_name}
          </Link>
          <h1 className="text-3xl font-bold tracking-tighter text-text-primary mb-1">{album.title}</h1>
          <div className="flex items-center gap-3 text-xs font-mono text-text-muted mt-1">
            {album.year && <span>{album.year}</span>}
            {album.record_type && <><span className="text-text-faint">|</span><span className="capitalize">{album.record_type}</span></>}
            <span className="text-text-faint">|</span>
            <span>{tracks.length} tracks</span>
            {totalMin > 0 && <><span className="text-text-faint">|</span><span>{totalMin} min</span></>}
          </div>
          <div className="flex items-center gap-2 mt-3">
            <button
              onClick={() => playQueue(albumQueue)}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-accent hover:bg-accent/80 rounded text-black text-xs font-semibold transition-colors"
              aria-label="Play album"
            >
              <Play size={12} className="fill-current" /> Play
            </button>
            <button
              onClick={() => {
                const shuffled = [...albumQueue].sort(() => Math.random() - 0.5);
                playQueue(shuffled);
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 border border-border-strong hover:border-border-strong rounded text-text-secondary text-xs font-mono transition-colors"
              aria-label="Shuffle album"
            >
              <Shuffle size={12} /> Shuffle
            </button>
            <button
              onClick={() => enqueueNext(albumQueue)}
              className="flex items-center gap-1.5 px-3 py-1.5 border border-border-strong hover:border-border-strong rounded text-text-secondary text-xs font-mono transition-colors"
              aria-label="Add to queue"
            >
              <ListPlus size={12} /> Queue
            </button>
          </div>
          {coverEditing && (
            <div className="flex items-center gap-2 mt-2">
              <input
                type="url"
                value={coverInput}
                onChange={e => setCoverInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') handleSaveCover();
                  if (e.key === 'Escape') { setCoverEditing(false); setCoverInput(''); }
                }}
                placeholder="Paste album image URL..."
                className="flex-1 bg-surface border border-border-strong rounded px-2 py-1 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent font-mono"
                autoFocus
              />
              <button
                onClick={handleSaveCover}
                disabled={coverSaving || !coverInput.trim()}
                className="flex items-center gap-1 px-2 py-1 bg-accent hover:bg-accent/80 disabled:opacity-40 rounded text-black text-xs font-semibold transition-colors"
              >
                <Check size={12} />
                Save
              </button>
              <button
                onClick={() => { setCoverEditing(false); setCoverInput(''); }}
                className="px-2 py-1 border border-border-strong hover:border-border-strong rounded text-text-muted text-xs font-mono transition-colors"
              >
                Cancel
              </button>
            </div>
          )}
          <div className="flex items-center gap-2 mt-2">
            <ConfidenceBadge value={album.match_confidence} />
            <SourceChip backend={album.source_platform} />
            {album.needs_verification === 1 && (
              <button
                onClick={openFixMatchModal}
                className="font-mono text-[10px] text-warning-text border border-accent/30 px-1.5 py-0.5 rounded hover:bg-accent/10 transition-colors"
                title="Open Fix Match"
              >
                verify
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="border-t border-border-subtle" />

      {/* Track list */}
      <div className="px-8 py-5">
        <div className="grid grid-cols-[1.75rem_2rem_1fr_6rem_3.5rem_auto_auto_auto_auto] gap-3 items-center px-2 py-1.5 border-b border-border-subtle mb-1">
          <span />
          <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest text-right">#</span>
          <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest">Title</span>
          <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest">Rating</span>
          <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest text-right">Dur</span>
          <span />
          <span />
          <span />
          <span />
        </div>
        {tracks.map(t => (
          <div
            key={t.id}
            onDoubleClick={() => playTrackNow(t.id)}
            className="group relative grid grid-cols-[1.75rem_2rem_1fr_6rem_3.5rem_auto_auto_auto_auto] gap-3 items-center px-2 py-2 hover:bg-surface rounded-sm transition-colors"
          >
            <button
              onClick={() => playTrackNow(t.id)}
              className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-accent transition-all"
              aria-label={`Play ${t.title}`}
              title="Play now"
            >
              <Play size={14} />
            </button>
            <span className="font-mono text-xs text-text-muted group-hover:text-accent/80 tabular-nums text-right">
              {String(t.track_number ?? 0).padStart(2, '0')}
            </span>
            <span className="text-sm text-text-primary group-hover:text-accent truncate">{t.title}</span>
            <StarRating value={ratings[t.id] ?? 0} onChange={v => handleRate(t.id, v)} size={12} />
            <span className="font-mono text-xs text-text-muted group-hover:text-accent/80 tabular-nums text-right">{formatDuration(t.duration)}</span>
            <AudioQualityBadge bit_depth={t.bit_depth} sample_rate={t.sample_rate} codec={t.codec} bitrate={t.bitrate} />
            <button
              onClick={() => {
                const item = albumQueue.find(p => p.id === t.id);
                if (item) enqueueNext([item]);
              }}
              className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-accent transition-all"
              title="Add to queue"
            >
              <ListPlus size={14} />
            </button>
            <button
              onClick={() => picker.openPicker(t)}
              className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-accent transition-all"
              title="Add to playlist"
              aria-label={`Add ${t.title} to playlist`}
            >
              <span className="font-mono text-[10px]">PL</span>
            </button>
            <div data-track-menu-root="true" className="relative">
            <button
              onClick={() => setOpenTrackMenuId(prev => (prev === t.id ? null : t.id))}
              className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-text-secondary transition-all"
              title="More actions"
              aria-label={`More actions for ${t.title}`}
            >
              <MoreHorizontal size={14} />
            </button>
            {openTrackMenuId === t.id && (
              <div className="absolute right-0 top-6 z-30 w-44 bg-surface border border-border-input rounded-sm shadow-xl py-1">
                <button
                  onClick={() => {
                    const shuffled = [...albumQueue].sort(() => Math.random() - 0.5);
                    playQueue(shuffled);
                    setOpenTrackMenuId(null);
                  }}
                  className="w-full text-left px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary hover:bg-surface-raised transition-colors"
                >
                  Shuffle
                </button>
                <button
                  onClick={() => {
                    const item = albumQueue.find(p => p.id === t.id);
                    if (item) enqueueNext([item]);
                    setOpenTrackMenuId(null);
                  }}
                  className="w-full text-left px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary hover:bg-surface-raised transition-colors"
                >
                  Play next
                </button>
                <button
                  onClick={() => {
                    const item = albumQueue.find(p => p.id === t.id);
                    if (item) addToQueue([item]);
                    setOpenTrackMenuId(null);
                  }}
                  className="w-full text-left px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary hover:bg-surface-raised transition-colors"
                >
                  Add to queue
                </button>
                <button
                  onClick={() => {
                    picker.openPicker(t);
                    setOpenTrackMenuId(null);
                  }}
                  className="w-full text-left px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary hover:bg-surface-raised transition-colors"
                >
                  Add to {'>'} Playlist
                </button>
                <div className="h-px bg-surface-overlay my-1" />
                <div className="px-3 py-1 text-[10px] text-text-muted/70 uppercase tracking-wider">Future</div>
                <button disabled className="w-full text-left px-3 py-1.5 text-xs text-text-muted/40 cursor-not-allowed">Refresh (coming soon)</button>
                <button
                  onClick={openFixMatchModal}
                  className="w-full text-left px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary hover:bg-surface-raised transition-colors"
                >
                  Fix match
                </button>
                <button disabled className="w-full text-left px-3 py-1.5 text-xs text-text-muted/40 cursor-not-allowed">Delete (coming soon)</button>
                <button disabled className="w-full text-left px-3 py-1.5 text-xs text-text-muted/40 cursor-not-allowed">View history (coming soon)</button>
              </div>
            )}
            </div>
          </div>
        ))}
      </div>

      {fixMatchOpen && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
          <div className="w-full max-w-5xl bg-base border border-border-input p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-text-primary">Fix Match</h3>
                <p className="text-xs text-text-muted mt-1">
                  {fixMatchContext.artist_name} - {fixMatchContext.title}
                </p>
                <p className="text-[11px] text-text-muted mt-1 font-mono">
                  confidence {Math.round(fixMatchContext.match_confidence ?? 0)}% | tracks {fixMatchContext.track_count}
                </p>
                <p className="text-[11px] text-text-muted mt-1 font-mono">
                  current IDs - iTunes: {fixMatchContext.itunes_album_id || '(none)'} | Deezer: {fixMatchContext.deezer_id || '(none)'}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => void loadFixMatchCandidates()}
                  disabled={fixMatchLoading || fixMatchSaving}
                  className="px-2.5 py-1 text-xs border border-border-strong text-text-muted hover:text-text-primary transition-colors disabled:opacity-40"
                >
                  {fixMatchLoading ? 'Loading...' : 'Reload'}
                </button>
                <button
                  onClick={closeFixMatchModal}
                  disabled={fixMatchSaving}
                  className="p-1 border border-border-strong text-text-muted hover:text-text-primary transition-colors disabled:opacity-40"
                  aria-label="Close fix match"
                >
                  <X size={14} />
                </button>
              </div>
            </div>

            {fixMatchError && (
              <p className="text-xs text-danger mt-3">{fixMatchError}</p>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-4">
              {(['itunes', 'deezer'] as const).map(source => {
                const currentSourceId = source === 'itunes' ? fixMatchContext.itunes_album_id : fixMatchContext.deezer_id;
                const candidates = fixMatchCandidates[source];
                return (
                  <div key={source} className="border border-border-subtle bg-surface-sunken p-3 min-h-[360px]">
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <h4 className="text-xs font-mono uppercase tracking-wider text-text-secondary">
                        {fixMatchSourceLabel[source]} candidates
                      </h4>
                      <button
                        onClick={() => void rejectFixMatchSource(source)}
                        disabled={fixMatchSaving}
                        className="px-2 py-1 text-[11px] border border-border-strong text-text-muted hover:text-text-primary transition-colors disabled:opacity-40"
                      >
                        Reject current
                      </button>
                    </div>
                    <p className="text-[11px] text-text-muted font-mono mb-2 truncate">
                      current {fixMatchSourceLabel[source]} ID: {currentSourceId || '(none)'}
                    </p>
                    <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
                      {!fixMatchLoading && candidates.length === 0 && (
                        <p className="text-xs text-text-muted">No candidates found.</p>
                      )}
                      {candidates.map(candidate => {
                        const scorePct = Math.round((candidate.candidate_score ?? 0) * 100);
                        const isCurrent = !!currentSourceId && String(currentSourceId) === String(candidate.candidate_id);
                        return (
                          <div key={`${source}:${candidate.candidate_id}`} className="border border-border bg-surface p-2">
                            <div className="flex items-start gap-2">
                              <div className="w-11 h-11 bg-surface-raised border border-border flex-shrink-0 overflow-hidden flex items-center justify-center">
                                {candidate.artwork_url ? (
                                  <img
                                    src={getImageUrl(candidate.artwork_url)}
                                    alt={candidate.candidate_title}
                                    className="w-full h-full object-cover"
                                  />
                                ) : (
                                  <Disc size={14} className="text-text-muted" />
                                )}
                              </div>
                              <div className="min-w-0 flex-1">
                                <p className="text-xs text-text-primary truncate">{candidate.candidate_title}</p>
                                <p className="text-[11px] text-text-muted font-mono">
                                  score {scorePct}% | tracks {candidate.track_count ?? '-'}
                                </p>
                                {candidate.reasons.length > 0 && (
                                  <p className="text-[10px] text-text-muted/80 font-mono truncate">
                                    {candidate.reasons.join(', ')}
                                  </p>
                                )}
                              </div>
                              <div className="flex flex-col items-end gap-1">
                                {isCurrent && (
                                  <span className="text-[10px] font-mono text-green-400 border border-green-400/30 px-1 py-0.5">
                                    current
                                  </span>
                                )}
                                <button
                                  onClick={() => void confirmFixMatchCandidate(source, candidate.candidate_id)}
                                  disabled={fixMatchSaving}
                                  className="px-2 py-1 text-[11px] bg-accent text-black hover:bg-accent/80 transition-colors disabled:opacity-40"
                                >
                                  Confirm
                                </button>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                onClick={closeFixMatchModal}
                disabled={fixMatchSaving}
                className="px-3 py-1.5 text-xs border border-border-strong text-text-muted hover:text-text-primary transition-colors disabled:opacity-40"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      <PlaylistPickerModal {...picker} />

      <div className="h-4" />
    </div>
  );
}
