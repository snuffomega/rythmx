/**
 * VinylPlayerScreen — immersive vinyl jacket aesthetic.
 *
 * The entire player floats centered in a dark canvas — content is naturally
 * sized and vertically/horizontally centered, with open dark space around it.
 * Nothing stretches to fill the screen.
 *
 * Inner block (max-w-[520px], centered):
 *   header (minimize · standard-view)
 *   carousel  (prev smaller · current LP · next larger)
 *   track info (title / artist · album / waveform / stars / quality)
 *   scrobble bar
 *   button row: [misc left] [Prev · Play · Next center] [volume · queue right]
 *
 * Artwork fix: useImage lives inside ArtCard; parent passes key={track?.id}
 * so React remounts on skip → fresh image every track change.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Play, Pause, SkipBack, SkipForward,
  Shuffle, Repeat, Repeat1, Settings,
  Disc, Star, Heart, ListMusic, Mic2,
  ChevronDown, List, ListPlus, Volume2, X, Maximize2, Minimize2, MoreHorizontal, Trash2, GripVertical,
} from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { usePlayerStore } from '../stores/usePlayerStore';
import { AudioQualityBadge, TrackArt } from './common';
import { libraryBrowseApi, libraryPlaylistsApi } from '../services/api';
import { useToastStore } from '../stores/useToastStore';
import type { LibPlaylist } from '../types';

interface VinylPlayerScreenProps {
  isPlaying: boolean;
  onPlayPause: () => void;
  onMinimize: () => void;
  onSeek: (seconds: number) => void;
  onVolumeChange: (vol: number) => void;
}

// ── Decorative waveform ──────────────────────────────────────────────────────
const WAVE_HEIGHTS = [4, 7, 11, 15, 9, 18, 13, 7, 20, 12, 16, 9, 5, 14, 10, 7, 17, 12, 8, 18, 13, 9, 4, 14, 10, 7, 18, 12, 6, 15];

function Waveform() {
  return (
    <div className="flex items-center justify-center gap-[2.5px]" style={{ height: '16px' }}>
      {WAVE_HEIGHTS.map((h, i) => (
        <div
          key={i}
          className="rounded-full flex-shrink-0"
          style={{ width: '2px', height: `${Math.min(h, 14)}px`, background: 'rgba(212,245,60,0.20)' }}
        />
      ))}
    </div>
  );
}

// ── Star rating ──────────────────────────────────────────────────────────────
function StarRating({ rating, onChange }: { rating: number; onChange: (n: number) => void }) {
  const [hover, setHover] = useState(0);
  return (
    <div className="flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map(n => (
        <button
          key={n}
          onClick={() => onChange(n === rating ? 0 : n)}
          onMouseEnter={() => setHover(n)}
          onMouseLeave={() => setHover(0)}
          aria-label={`Rate ${n} star${n !== 1 ? 's' : ''}`}
          className="p-0.5 transition-all active:scale-90"
        >
          <Star
            size={18}
            className={`transition-colors ${n <= (hover || rating) ? 'text-accent' : 'text-[#2e2e2e]'}`}
            fill={n <= (hover || rating) ? 'currentColor' : 'none'}
          />
        </button>
      ))}
    </div>
  );
}

function formatTrackDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '--:--';
  const total = Math.floor(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

// ── Main ─────────────────────────────────────────────────────────────────────

export function VinylPlayerScreen({
  isPlaying, onPlayPause, onMinimize, onSeek, onVolumeChange,
}: VinylPlayerScreenProps) {
  const navigate    = useNavigate();
  const toastSuccess = useToastStore(s => s.success);
  const toastError = useToastStore(s => s.error);
  const progressRef = useRef<HTMLDivElement>(null);
  const queuePanelRef = useRef<HTMLDivElement>(null);
  const queueButtonRef = useRef<HTMLButtonElement>(null);

  const [starRating, setStarRating] = useState(0);
  const [liked,      setLiked]      = useState(false);
  const [showQueue,  setShowQueue]  = useState(false);
  const [queueExpanded, setQueueExpanded] = useState(false);
  const [queueMenuIndex, setQueueMenuIndex] = useState<number | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dropIndex, setDropIndex] = useState<number | null>(null);
  const [coverDirection, setCoverDirection] = useState<1 | -1>(1);
  const [coverTransitionActive, setCoverTransitionActive] = useState(false);
  const [showVolume, setShowVolume] = useState(false);
  const [artistNavLoading, setArtistNavLoading] = useState(false);
  const [albumNavLoading, setAlbumNavLoading] = useState(false);
  const [playlistOptions, setPlaylistOptions] = useState<LibPlaylist[]>([]);
  const [playlistPickerOpen, setPlaylistPickerOpen] = useState(false);
  const [playlistPickerLoading, setPlaylistPickerLoading] = useState(false);
  const [playlistPickerSaving, setPlaylistPickerSaving] = useState(false);
  const [playlistPickerError, setPlaylistPickerError] = useState<string | null>(null);
  const [playlistTrack, setPlaylistTrack] = useState<{ id: string; title: string } | null>(null);
  const [selectedPlaylistId, setSelectedPlaylistId] = useState('');

  const {
    currentTrack, queue, queueIndex,
    position, duration, volume,
    formattedPosition, formattedDuration,
    shuffle, repeatMode,
    nextTrack, prevTrack,
    toggleShuffle, toggleRepeat,
    setVolume: storeSetVolume,
  } = usePlayerStore();
  const previousQueueIndexRef = useRef(queueIndex);

  const prevTrackItem = queueIndex > 0 ? queue[queueIndex - 1] : null;
  const nextTrackItem = (() => {
    if (shuffle) return null;
    if (repeatMode === 'all' && queueIndex === queue.length - 1) return queue[0] ?? null;
    return queue[queueIndex + 1] ?? null;
  })();

  const progressPct = duration > 0 ? (position / duration) * 100 : 0;
  const effectiveDuration = duration > 0 ? duration : (currentTrack?.duration ?? 0);
  const coverOffset = coverTransitionActive ? coverDirection * 18 : 0;

  const openPlaylistPicker = useCallback(async (track: { id: string; title: string }) => {
    setPlaylistTrack(track);
    setPlaylistPickerOpen(true);
    setPlaylistPickerLoading(true);
    setPlaylistPickerSaving(false);
    setPlaylistPickerError(null);
    try {
      const list = await libraryPlaylistsApi.list();
      setPlaylistOptions(list);
      setSelectedPlaylistId(list[0]?.id ?? '');
      if (list.length === 0) {
        setPlaylistPickerError('No playlists found. Sync or create one in Library > Playlists first.');
      }
    } catch (err) {
      setPlaylistOptions([]);
      setSelectedPlaylistId('');
      setPlaylistPickerError(err instanceof Error ? err.message : 'Failed to load playlists');
    } finally {
      setPlaylistPickerLoading(false);
    }
  }, []);

  const closePlaylistPicker = useCallback(() => {
    setPlaylistPickerOpen(false);
    setPlaylistPickerLoading(false);
    setPlaylistPickerSaving(false);
    setPlaylistPickerError(null);
    setPlaylistTrack(null);
    setSelectedPlaylistId('');
  }, []);

  const confirmAddToPlaylist = useCallback(async () => {
    if (!playlistTrack || !selectedPlaylistId) {
      return;
    }
    setPlaylistPickerSaving(true);
    setPlaylistPickerError(null);
    try {
      const result = await libraryPlaylistsApi.addTracks(selectedPlaylistId, [playlistTrack.id]);
      const selected = playlistOptions.find(p => p.id === selectedPlaylistId);
      toastSuccess(
        `Added "${playlistTrack.title}" to "${selected?.name ?? selectedPlaylistId}" (${result.track_count} tracks)`
      );
      closePlaylistPicker();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to add track to playlist';
      setPlaylistPickerError(msg);
      toastError(msg);
    } finally {
      setPlaylistPickerSaving(false);
    }
  }, [playlistTrack, selectedPlaylistId, playlistOptions, toastSuccess, toastError, closePlaylistPicker]);

  function handleProgressClick(e: React.MouseEvent<HTMLDivElement>) {
    if (!progressRef.current || effectiveDuration <= 0) return;
    const rect = progressRef.current.getBoundingClientRect();
    onSeek(Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) * effectiveDuration);
  }

  function handleProgressMouseDown(e: React.MouseEvent<HTMLDivElement>) {
    if (!progressRef.current || effectiveDuration <= 0) return;

    const seekAt = (clientX: number) => {
      if (!progressRef.current || effectiveDuration <= 0) return;
      const rect = progressRef.current.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      onSeek(pct * effectiveDuration);
    };

    seekAt(e.clientX);
    const onMove = (event: MouseEvent) => seekAt(event.clientX);
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }

  function handleVolumeSlider(val: number) {
    const v = Math.max(0, Math.min(1, val));
    storeSetVolume(v);
    onVolumeChange(v);
  }

  async function handleArtistClick() {
    const artistName = currentTrack?.artist?.trim();
    if (!artistName || artistNavLoading) return;
    setArtistNavLoading(true);
    try {
      const res = await libraryBrowseApi.getArtists({ q: artistName, per_page: 25 });
      const exact = res.artists.find((a) => a.name.toLowerCase() === artistName.toLowerCase()) ?? res.artists[0];
      if (exact) {
        onMinimize();
        navigate({ to: '/library/artist/$id', params: { id: exact.id } });
      } else {
        onMinimize();
        navigate({ to: '/library' });
      }
    } finally {
      setArtistNavLoading(false);
    }
  }

  async function handleAlbumClick() {
    const albumTitle = currentTrack?.album?.trim();
    const artistName = currentTrack?.artist?.trim().toLowerCase();
    if (!albumTitle || albumNavLoading) return;
    setAlbumNavLoading(true);
    try {
      const res = await libraryBrowseApi.getAlbums({ q: albumTitle, per_page: 50 });
      const exactTitle = res.albums.filter((a) => a.title.toLowerCase() === albumTitle.toLowerCase());
      const exact = exactTitle.find((a) => !artistName || a.artist_name.toLowerCase() === artistName)
        ?? exactTitle[0]
        ?? res.albums[0];
      if (exact) {
        onMinimize();
        navigate({ to: '/library/album/$id', params: { id: exact.id } });
      } else {
        onMinimize();
        navigate({ to: '/library' });
      }
    } finally {
      setAlbumNavLoading(false);
    }
  }

  function handleQueueTrackRemove(index: number) {
    usePlayerStore.getState().removeFromQueue(index);
    setQueueMenuIndex((curr) => (curr === index ? null : curr));
  }

  function handleQueueTrackPlayNext(index: number) {
    const { queueIndex: activeIndex } = usePlayerStore.getState();
    if (activeIndex < 0 || index === activeIndex || index === activeIndex + 1) return;
    const target = activeIndex + 1;
    const toIndex = index < target ? target - 1 : target;
    usePlayerStore.getState().moveQueueItem(index, toIndex);
    setQueueMenuIndex(null);
  }

  function clearQueueDragState() {
    setDragIndex(null);
    setDropIndex(null);
  }

  function handleQueueDragStart(event: React.DragEvent<HTMLElement>, index: number) {
    setQueueMenuIndex(null);
    setDragIndex(index);
    setDropIndex(index);
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(index));
  }

  function handleQueueDragOver(event: React.DragEvent<HTMLElement>, index: number) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    if (dropIndex !== index) {
      setDropIndex(index);
    }
  }

  function handleQueueDrop(event: React.DragEvent<HTMLElement>, index: number) {
    event.preventDefault();
    if (dragIndex === null) {
      clearQueueDragState();
      return;
    }
    if (dragIndex !== index) {
      usePlayerStore.getState().moveQueueItem(dragIndex, index);
    }
    clearQueueDragState();
  }

  function handleSettings() {
    setShowQueue(false);
    setQueueExpanded(false);
    setQueueMenuIndex(null);
    clearQueueDragState();
    onMinimize();
    navigate({ to: '/settings' });
  }

  function handleMinimize() {
    setShowQueue(false);
    setQueueExpanded(false);
    setQueueMenuIndex(null);
    clearQueueDragState();
    onMinimize();
  }

  useEffect(() => {
    const previousIndex = previousQueueIndexRef.current;
    if (queueIndex < 0 || queue.length < 2 || previousIndex === queueIndex) {
      previousQueueIndexRef.current = queueIndex;
      return;
    }

    let direction: 1 | -1 = queueIndex > previousIndex ? 1 : -1;
    if (repeatMode === 'all') {
      if (previousIndex === queue.length - 1 && queueIndex === 0) {
        direction = 1;
      } else if (previousIndex === 0 && queueIndex === queue.length - 1) {
        direction = -1;
      }
    }

    setCoverDirection(direction);
    setCoverTransitionActive(true);
    const timer = window.setTimeout(() => setCoverTransitionActive(false), 240);
    previousQueueIndexRef.current = queueIndex;
    return () => window.clearTimeout(timer);
  }, [queueIndex, queue.length, repeatMode]);

  useEffect(() => {
    if (!showQueue) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setShowQueue(false);
        setQueueExpanded(false);
        setQueueMenuIndex(null);
        clearQueueDragState();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [showQueue]);

  useEffect(() => {
    if (!showQueue || queueExpanded) return;
    const onMouseDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (queuePanelRef.current?.contains(target)) return;
      if (queueButtonRef.current?.contains(target)) return;
      setShowQueue(false);
      setQueueMenuIndex(null);
      clearQueueDragState();
    };
    document.addEventListener('mousedown', onMouseDown);
    return () => document.removeEventListener('mousedown', onMouseDown);
  }, [showQueue, queueExpanded]);

  return (
    // ── Outer canvas — full dark screen, content centered ─────────────────
    // overflow-auto so the queue panel can expand downward without clipping
    <div className="h-full w-full bg-base flex items-center justify-center overflow-auto py-8">

      {/* ── Floating player block — 80% width, 10% negative space each side ── */}
      <div className="flex flex-col w-full max-w-[760px] px-[10%] relative">

        {/* ── Header ──────────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between mb-5">
          <button
            onClick={handleMinimize}
            className="flex items-center gap-1.5 text-text-muted hover:text-text-secondary
                       transition-all active:scale-95 group"
            aria-label="Minimize player"
          >
            <ChevronDown size={20} />
            <span className="text-[10px] font-mono tracking-wide opacity-0 group-hover:opacity-60 transition-opacity">
              Minimize
            </span>
          </button>
          <div className="w-5" />
        </div>

        {/* ── Three-cover carousel ────────────────────────────────────────── */}
        {/* Left prev smaller, right next ~30% larger — asymmetric depth     */}
        <div className="flex items-center justify-center mb-6" style={{ perspective: '1000px' }}>
          <div className="flex items-center justify-center gap-4" style={{ transformStyle: 'preserve-3d' }}>

            {/* Previous — smaller, angled left */}
            <button
              onClick={() => prevTrackItem && prevTrack()}
              disabled={!prevTrackItem}
              aria-label="Previous track"
              className="flex-shrink-0 rounded-xl overflow-hidden focus:outline-none
                         disabled:cursor-default transition-all active:scale-95"
              style={{
                width: 'clamp(90px, 11vw, 130px)', height: 'clamp(90px, 11vw, 130px)',
                transform:  `translateX(${-8 - (coverOffset * 0.55)}px) rotateY(28deg) scale(0.85)`,
                opacity:    prevTrackItem ? (coverTransitionActive ? 0.62 : 0.50) : 0.08,
                border:     '1px solid rgba(255,255,255,0.07)',
                boxShadow:  '-6px 10px 28px rgba(0,0,0,0.70)',
                transition: 'opacity 0.24s ease-out, transform 0.24s ease-out',
              }}
            >
              <TrackArt
                key={prevTrackItem?.id ?? 'empty-prev'}
                thumbUrl={prevTrackItem?.thumb_url ?? null}
                thumbHash={prevTrackItem?.thumb_hash ?? null}
                title={prevTrackItem?.album ?? ''}
                artist={prevTrackItem?.artist ?? ''}
                size="fill"
                discSize={18}
                draggable={false}
              />
            </button>

            {/* Current — LP sleeve, dominant */}
            <div
              className="flex-shrink-0 rounded-2xl overflow-hidden relative"
              style={{
                width: 'clamp(280px, 42vw, 460px)', height: 'clamp(280px, 42vw, 460px)',
                transform: `translateX(${coverOffset}px) scale(${coverTransitionActive ? 0.985 : 1})`,
                opacity: coverTransitionActive ? 0.88 : 1,
                border:     '1.5px solid rgba(255,255,255,0.07)',
                boxShadow:  '0 28px 70px rgba(0,0,0,0.90), 0 8px 20px rgba(0,0,0,0.70), inset 0 1px 0 rgba(255,255,255,0.05)',
                transition: 'opacity 0.24s ease-out, transform 0.24s ease-out',
              }}
            >
              <TrackArt
                key={currentTrack?.id ?? 'empty-curr'}
                thumbUrl={currentTrack?.thumb_url ?? null}
                thumbHash={currentTrack?.thumb_hash ?? null}
                title={currentTrack?.album ?? ''}
                artist={currentTrack?.artist ?? ''}
                size="fill"
                discSize={48}
                draggable={false}
              />
              <div
                className="absolute inset-x-0 top-0 h-px pointer-events-none"
                style={{ background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.12) 50%, transparent)' }}
              />
            </div>

            {/* Next — ~30% larger than prev, angled right */}
            <button
              onClick={() => nextTrackItem && nextTrack()}
              disabled={!nextTrackItem}
              aria-label="Next track"
              className="flex-shrink-0 rounded-xl overflow-hidden focus:outline-none
                         disabled:cursor-default transition-all active:scale-95"
              style={{
                width: 'clamp(118px, 14vw, 170px)', height: 'clamp(118px, 14vw, 170px)',
                transform:  `translateX(${8 - (coverOffset * 0.55)}px) rotateY(-28deg) scale(0.90)`,
                opacity:    nextTrackItem ? (coverTransitionActive ? 0.70 : 0.58) : 0.08,
                border:     '1px solid rgba(255,255,255,0.08)',
                boxShadow:  '6px 10px 28px rgba(0,0,0,0.70)',
                transition: 'opacity 0.24s ease-out, transform 0.24s ease-out',
              }}
            >
              <TrackArt
                key={nextTrackItem?.id ?? 'empty-next'}
                thumbUrl={nextTrackItem?.thumb_url ?? null}
                thumbHash={nextTrackItem?.thumb_hash ?? null}
                title={nextTrackItem?.album ?? ''}
                artist={nextTrackItem?.artist ?? ''}
                size="fill"
                discSize={18}
                draggable={false}
              />
            </button>

          </div>
        </div>

        {/* ── Track info ──────────────────────────────────────────────────── */}
        <div className="text-center mb-4">
          <p className="text-[22px] font-semibold text-text-primary leading-tight truncate">
            {currentTrack?.title ?? 'Nothing playing'}
          </p>
          <div className="mt-1 flex items-center justify-center gap-1 font-mono text-[13px] text-text-secondary leading-tight min-h-[18px]">
            {currentTrack ? (
              <>
                <button
                  onClick={handleArtistClick}
                  disabled={artistNavLoading || !currentTrack.artist}
                  className="truncate max-w-[240px] hover:text-accent transition-colors disabled:cursor-default disabled:hover:text-text-secondary"
                  title={currentTrack.artist}
                >
                  {currentTrack.artist}
                </button>
                {currentTrack.album && (
                  <>
                    <span className="text-text-muted">-</span>
                    <button
                      onClick={handleAlbumClick}
                      disabled={albumNavLoading}
                      className="truncate max-w-[240px] hover:text-accent transition-colors disabled:cursor-default disabled:hover:text-text-secondary"
                      title={currentTrack.album}
                    >
                      {currentTrack.album}
                    </button>
                  </>
                )}
              </>
            ) : (
              <span>-</span>
            )}
          </div>
          {currentTrack && (
            <div className="mt-2 flex justify-center">
              <AudioQualityBadge
                codec={currentTrack.codec}
                bitrate={currentTrack.bitrate}
                bit_depth={currentTrack.bit_depth}
                sample_rate={currentTrack.sample_rate}
              />
            </div>
          )}
          <div className="mt-2 flex justify-center">
            <Waveform />
          </div>
          <div className="mt-2 flex justify-center">
            <StarRating rating={starRating} onChange={setStarRating} />
          </div>
        </div>

        {/* ── Scrobble bar ────────────────────────────────────────────────── */}
        <div className="flex items-center gap-3 mb-5">
          <span className="font-mono text-[10px] text-text-muted tabular-nums w-9 text-right flex-shrink-0">
            {formattedPosition}
          </span>
          <div
            ref={progressRef}
            onMouseDown={handleProgressMouseDown}
            onClick={handleProgressClick}
            className="flex-1 h-4 relative cursor-pointer group flex items-center select-none"
          >
            <div className="absolute inset-x-0 h-[5px] bg-[#1c1c1c] rounded-full overflow-hidden pointer-events-none">
              <div
                className="h-full bg-accent rounded-full"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <div
              className="absolute top-1/2 -translate-y-1/2 w-3.5 h-3.5 bg-accent rounded-full opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
              style={{ left: `${progressPct}%`, marginLeft: '-7px' }}
            />
          </div>
          <span className="font-mono text-[10px] text-text-muted tabular-nums w-9 flex-shrink-0">
            {formattedDuration}
          </span>
        </div>

        {/* ── Button row ───────────────────────────────────────────────────── */}
        <div className="relative grid grid-cols-[1fr_auto_1fr] items-center gap-3 min-h-[72px]">

          {/* Volume popup — floats above volume button (stays absolute/upward) */}
          {showVolume && (
            <div
              className="absolute bottom-full mb-3 bg-[#0d0d0d] border border-[#1e1e1e]
                         rounded-xl px-5 py-4 shadow-2xl z-30 flex flex-col gap-3"
              style={{ right: '52px', width: '200px' }}
            >
              <div className="flex items-center justify-between">
                <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest">Volume</span>
                <span className="font-mono text-[12px] text-text-secondary tabular-nums">
                  {Math.round(volume * 100)}%
                </span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={Math.round(volume * 100)}
                onChange={e => handleVolumeSlider(Number(e.target.value) / 100)}
                aria-label="Volume"
                className="w-full cursor-pointer"
                style={{ height: '6px', accentColor: '#D4F53C' }}
              />
            </div>
          )}

          {/* Left — misc controls */}
          <div className="flex items-center justify-start">
            <div className="grid grid-cols-2 gap-1.5 w-fit">
            <button
              onClick={() => setLiked(v => !v)}
              aria-label={liked ? 'Unlike' : 'Like'}
              className={`p-2 rounded-lg transition-all active:scale-90 ${liked ? 'text-danger' : 'text-text-muted hover:text-text-secondary'}`}
            >
              <Heart size={17} fill={liked ? 'currentColor' : 'none'} />
            </button>
            <button disabled title="Lyrics - coming soon" className="p-2 rounded-lg text-text-muted opacity-25 cursor-not-allowed">
              <Mic2 size={17} />
            </button>
            <button disabled title="Live - coming soon" className="p-2 rounded-lg text-text-muted opacity-25 cursor-not-allowed">
              <ListMusic size={17} />
            </button>
            <button
              onClick={handleSettings}
              aria-label="Settings"
              className="p-2 rounded-lg text-text-muted hover:text-text-secondary transition-all active:scale-90"
            >
              <Settings size={17} />
            </button>
            </div>
          </div>

          {/* Center — primary transport */}
          <div className="flex items-center justify-center">
            <div className="flex items-center justify-center gap-4 sm:gap-5 flex-nowrap">
            <button
              onClick={toggleShuffle}
              aria-label={shuffle ? 'Shuffle on' : 'Shuffle off'}
              className={`text-text-secondary hover:text-text-primary transition-all active:scale-90 ${shuffle ? 'text-accent' : ''}`}
            >
              <Shuffle size={20} />
            </button>
            <button
              onClick={prevTrack}
              disabled={!currentTrack}
              aria-label="Previous"
              className="text-text-secondary hover:text-text-primary transition-all active:scale-90 disabled:opacity-25"
            >
              <SkipBack size={24} />
            </button>
            <button
              onClick={onPlayPause}
              disabled={!currentTrack}
              aria-label={isPlaying ? 'Pause' : 'Play'}
              className="w-[58px] h-[58px] rounded-full bg-accent hover:bg-accent/85
                         flex items-center justify-center flex-shrink-0
                         transition-all active:scale-90 disabled:opacity-25"
            >
              {isPlaying
                ? <Pause size={22} className="text-black" />
                : <Play  size={22} className="text-black ml-0.5" />}
            </button>
            <button
              onClick={nextTrack}
              disabled={!currentTrack}
              aria-label="Next"
              className="text-text-secondary hover:text-text-primary transition-all active:scale-90 disabled:opacity-25"
            >
              <SkipForward size={24} />
            </button>
            <button
              onClick={toggleRepeat}
              aria-label={
                repeatMode === 'off' ? 'Repeat off'
                  : repeatMode === 'all' ? 'Repeat all'
                    : 'Repeat one'
              }
              title={
                repeatMode === 'off' ? 'Repeat off'
                  : repeatMode === 'all' ? 'Repeat all'
                    : 'Repeat one'
              }
              className={`text-text-secondary hover:text-text-primary transition-all active:scale-90 ${repeatMode !== 'off' ? 'text-accent' : ''}`}
            >
              {repeatMode === 'one' ? <Repeat1 size={20} /> : <Repeat size={20} />}
            </button>
          </div>

          </div>
          {/* Right — volume + queue */}
          <div className="flex items-center justify-end gap-1">
            <button
              onClick={() => {
                setShowVolume(v => !v);
                setShowQueue(false);
                setQueueExpanded(false);
                setQueueMenuIndex(null);
                clearQueueDragState();
              }}
              aria-label="Volume"
              className={`p-2.5 rounded-lg transition-all active:scale-90 ${showVolume ? 'text-accent' : 'text-text-muted hover:text-text-secondary'}`}
            >
              <Volume2 size={21} />
            </button>
            <button
              ref={queueButtonRef}
              onClick={() => {
                setShowVolume(false);
                setQueueExpanded(false);
                setQueueMenuIndex(null);
                setShowQueue((open) => {
                  if (open) {
                    clearQueueDragState();
                  }
                  return !open;
                });
              }}
              aria-label="Toggle queue"
              className={`relative p-2.5 rounded-lg transition-all active:scale-90 ${showQueue ? 'text-accent' : 'text-text-muted hover:text-text-secondary'}`}
            >
              <List size={26} />
              {queue.length > 0 && (
                <span className="absolute top-1.5 right-1.5 w-3.5 h-3.5 rounded-full
                                 bg-accent text-black text-[8px] font-bold
                                 flex items-center justify-center leading-none tabular-nums">
                  {queue.length > 99 ? '99' : queue.length}
                </span>
              )}
            </button>
          </div>

          {showQueue && !queueExpanded && (
            <div
              ref={queuePanelRef}
              className="absolute top-full left-1/2 -translate-x-1/2 mt-3 w-[min(84vw,520px)]
                         border border-[#1e1e1e] rounded-xl overflow-hidden bg-[#0a0a0a] shadow-2xl z-30
                         transition-[transform,opacity,box-shadow] duration-200 ease-out"
            >
              <div className="flex items-center justify-between px-4 py-3 border-b border-[#1a1a1a]">
                <span className="font-mono text-[11px] text-text-muted uppercase tracking-widest">
                  Queue - {queue.length} track{queue.length !== 1 ? 's' : ''}
                </span>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => { setQueueMenuIndex(null); setQueueExpanded(true); }}
                    className="p-1 text-text-muted hover:text-text-primary transition-colors"
                    title="Expand queue"
                    aria-label="Expand queue"
                  >
                    <Maximize2 size={14} />
                  </button>
                  {queue.length > 0 && (
                    <button
                      onClick={() => { usePlayerStore.getState().clearQueue(); setQueueMenuIndex(null); clearQueueDragState(); }}
                      className="text-[10px] font-mono text-text-muted hover:text-text-secondary transition-colors"
                    >
                      Clear
                    </button>
                  )}
                  <button
                    onClick={() => { setShowQueue(false); setQueueExpanded(false); setQueueMenuIndex(null); clearQueueDragState(); }}
                    aria-label="Close queue"
                    className="p-1 text-text-muted hover:text-text-primary transition-colors"
                  >
                    <X size={14} />
                  </button>
                </div>
              </div>
              <div className="max-h-[32vh] overflow-y-auto">
                {queue.length === 0 ? (
                  <p className="text-[12px] text-text-muted font-mono text-center py-8 px-5 leading-relaxed">
                    Queue empty<br />play something from your library
                  </p>
                ) : (
                  <ul>
                    {queue.map((track, i) => (
                      <li key={`${track.id}-${i}`}>
                        <div
                          onDragOver={(event) => handleQueueDragOver(event, i)}
                          onDrop={(event) => handleQueueDrop(event, i)}
                          className={`group relative px-4 py-2.5 flex items-center gap-2.5 transition-colors ${
                            dropIndex === i && dragIndex !== null && dragIndex !== i
                              ? 'bg-[#151515] ring-1 ring-accent/40'
                              : 'hover:bg-[#111]'
                          }`}
                        >
                          <button
                            draggable
                            onDragStart={(event) => handleQueueDragStart(event, i)}
                            onDragEnd={clearQueueDragState}
                            className={`text-text-muted hover:text-text-secondary transition-colors ${
                              dragIndex === i ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
                            }`}
                            title="Drag to reorder"
                            aria-label={`Drag ${track.title} to reorder`}
                          >
                            <GripVertical size={13} />
                          </button>
                          <button
                            draggable
                            onDragStart={(event) => handleQueueDragStart(event, i)}
                            onDragEnd={clearQueueDragState}
                            onClick={() => { usePlayerStore.getState().playAt(i); setShowQueue(false); setQueueExpanded(false); clearQueueDragState(); }}
                            className="flex-1 min-w-0 text-left flex items-center gap-2.5 cursor-pointer hover:cursor-grab active:cursor-grabbing"
                          >
                            <span className="font-mono text-[10px] text-text-muted w-5 flex-shrink-0 text-right">
                              {i === queueIndex && isPlaying ? '>' : i + 1}
                            </span>
                            <div className="min-w-0 flex-1">
                              <p className={`text-[12px] truncate leading-tight ${i === queueIndex ? 'text-accent' : 'text-text-primary'}`}>
                                {track.title}
                              </p>
                              <p className="font-mono text-[10px] text-text-muted truncate leading-tight">
                                {track.artist}
                              </p>
                            </div>
                          </button>
                          <span className="font-mono text-[10px] text-text-muted tabular-nums w-11 text-right">
                            {formatTrackDuration(track.duration)}
                          </span>
                          <button
                            onClick={() => { setQueueMenuIndex(null); openPlaylistPicker(track); }}
                            className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-accent transition-all"
                            title="Add to playlist"
                            aria-label={`Add ${track.title} to playlist`}
                          >
                            <ListPlus size={13} />
                          </button>
                          <button
                            onClick={() => handleQueueTrackRemove(i)}
                            className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-danger transition-all"
                            title="Remove from queue"
                            aria-label={`Remove ${track.title} from queue`}
                          >
                            <Trash2 size={13} />
                          </button>
                          <button
                            onClick={() => setQueueMenuIndex((curr) => (curr === i ? null : i))}
                            className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-text-primary transition-all"
                            title="Queue item menu"
                            aria-label={`Open menu for ${track.title}`}
                          >
                            <MoreHorizontal size={13} />
                          </button>
                          {queueMenuIndex === i && (
                            <div className="absolute right-3 top-full mt-1 w-40 bg-[#0f0f0f] border border-[#222] rounded-md shadow-xl z-40">
                              <button
                                onClick={() => { usePlayerStore.getState().playAt(i); setQueueMenuIndex(null); setShowQueue(false); setQueueExpanded(false); clearQueueDragState(); }}
                                className="w-full px-3 py-1.5 text-left text-[11px] text-text-secondary hover:bg-[#161616] transition-colors"
                              >
                                Play now
                              </button>
                              <button
                                onClick={() => handleQueueTrackPlayNext(i)}
                                className="w-full px-3 py-1.5 text-left text-[11px] text-text-secondary hover:bg-[#161616] transition-colors"
                              >
                                Play next
                              </button>
                              <button
                                onClick={() => { setQueueMenuIndex(null); openPlaylistPicker(track); }}
                                className="w-full px-3 py-1.5 text-left text-[11px] text-text-secondary hover:bg-[#161616] transition-colors"
                              >
                                Add to playlist
                              </button>
                              <button
                                onClick={() => handleQueueTrackRemove(i)}
                                className="w-full px-3 py-1.5 text-left text-[11px] text-danger hover:bg-[#161616] transition-colors"
                              >
                                Remove
                              </button>
                              <button
                                disabled
                                className="w-full px-3 py-1.5 text-left text-[11px] text-text-muted opacity-60 cursor-not-allowed"
                              >
                                Report (soon)
                              </button>
                            </div>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}

        </div>

        {/* Queue expanded overlay */}
        {showQueue && queueExpanded && (
          <div
            className="fixed inset-0 z-30 bg-black/35 flex items-end justify-center px-4 pb-8"
            onClick={() => { setShowQueue(false); setQueueExpanded(false); setQueueMenuIndex(null); clearQueueDragState(); }}
          >
            <div
              className="w-full max-w-[720px] border border-[#1e1e1e] rounded-xl overflow-hidden bg-[#0a0a0a] shadow-2xl
                         transition-[transform,opacity,box-shadow] duration-200 ease-out"
              onClick={(event) => event.stopPropagation()}
            >
              <div className="flex items-center justify-between px-4 py-3 border-b border-[#1a1a1a]">
                <span className="font-mono text-[11px] text-text-muted uppercase tracking-widest">
                  Queue - {queue.length} track{queue.length !== 1 ? 's' : ''}
                </span>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => { setQueueMenuIndex(null); setQueueExpanded(false); clearQueueDragState(); }}
                    className="p-1 text-text-muted hover:text-text-primary transition-colors"
                    title="Collapse queue"
                    aria-label="Collapse queue"
                  >
                    <Minimize2 size={14} />
                  </button>
                  {queue.length > 0 && (
                    <button
                      onClick={() => { usePlayerStore.getState().clearQueue(); setQueueMenuIndex(null); clearQueueDragState(); }}
                      className="text-[10px] font-mono text-text-muted hover:text-text-secondary transition-colors"
                    >
                      Clear
                    </button>
                  )}
                  <button
                    onClick={() => { setShowQueue(false); setQueueExpanded(false); setQueueMenuIndex(null); clearQueueDragState(); }}
                    aria-label="Close queue"
                    className="p-1 text-text-muted hover:text-text-primary transition-colors"
                  >
                    <X size={14} />
                  </button>
                </div>
              </div>
              <div className="max-h-[55vh] overflow-y-auto">
                {queue.length === 0 ? (
                  <p className="text-[12px] text-text-muted font-mono text-center py-8 px-5 leading-relaxed">
                    Queue empty<br />play something from your library
                  </p>
                ) : (
                  <ul>
                    {queue.map((track, i) => (
                      <li key={`${track.id}-${i}`}>
                        <div
                          onDragOver={(event) => handleQueueDragOver(event, i)}
                          onDrop={(event) => handleQueueDrop(event, i)}
                          className={`group relative px-4 py-2.5 flex items-center gap-2.5 transition-colors ${
                            dropIndex === i && dragIndex !== null && dragIndex !== i
                              ? 'bg-[#151515] ring-1 ring-accent/40'
                              : 'hover:bg-[#111]'
                          }`}
                        >
                          <button
                            draggable
                            onDragStart={(event) => handleQueueDragStart(event, i)}
                            onDragEnd={clearQueueDragState}
                            className={`text-text-muted hover:text-text-secondary transition-colors ${
                              dragIndex === i ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
                            }`}
                            title="Drag to reorder"
                            aria-label={`Drag ${track.title} to reorder`}
                          >
                            <GripVertical size={13} />
                          </button>
                          <button
                            draggable
                            onDragStart={(event) => handleQueueDragStart(event, i)}
                            onDragEnd={clearQueueDragState}
                            onClick={() => { usePlayerStore.getState().playAt(i); setShowQueue(false); setQueueExpanded(false); clearQueueDragState(); }}
                            className="flex-1 min-w-0 text-left flex items-center gap-2.5 cursor-pointer hover:cursor-grab active:cursor-grabbing"
                          >
                            <span className="font-mono text-[10px] text-text-muted w-5 flex-shrink-0 text-right">
                              {i === queueIndex && isPlaying ? '>' : i + 1}
                            </span>
                            <div className="min-w-0 flex-1">
                              <p className={`text-[12px] truncate leading-tight ${i === queueIndex ? 'text-accent' : 'text-text-primary'}`}>
                                {track.title}
                              </p>
                              <p className="font-mono text-[10px] text-text-muted truncate leading-tight">
                                {track.artist}
                              </p>
                            </div>
                          </button>
                          <span className="font-mono text-[10px] text-text-muted tabular-nums w-11 text-right">
                            {formatTrackDuration(track.duration)}
                          </span>
                          <button
                            onClick={() => { setQueueMenuIndex(null); openPlaylistPicker(track); }}
                            className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-accent transition-all"
                            title="Add to playlist"
                            aria-label={`Add ${track.title} to playlist`}
                          >
                            <ListPlus size={13} />
                          </button>
                          <button
                            onClick={() => handleQueueTrackRemove(i)}
                            className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-danger transition-all"
                            title="Remove from queue"
                            aria-label={`Remove ${track.title} from queue`}
                          >
                            <Trash2 size={13} />
                          </button>
                          <button
                            onClick={() => setQueueMenuIndex((curr) => (curr === i ? null : i))}
                            className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-text-primary transition-all"
                            title="Queue item menu"
                            aria-label={`Open menu for ${track.title}`}
                          >
                            <MoreHorizontal size={13} />
                          </button>
                          {queueMenuIndex === i && (
                            <div className="absolute right-3 top-full mt-1 w-40 bg-[#0f0f0f] border border-[#222] rounded-md shadow-xl z-40">
                              <button
                                onClick={() => { usePlayerStore.getState().playAt(i); setQueueMenuIndex(null); setShowQueue(false); setQueueExpanded(false); clearQueueDragState(); }}
                                className="w-full px-3 py-1.5 text-left text-[11px] text-text-secondary hover:bg-[#161616] transition-colors"
                              >
                                Play now
                              </button>
                              <button
                                onClick={() => handleQueueTrackPlayNext(i)}
                                className="w-full px-3 py-1.5 text-left text-[11px] text-text-secondary hover:bg-[#161616] transition-colors"
                              >
                                Play next
                              </button>
                              <button
                                onClick={() => { setQueueMenuIndex(null); openPlaylistPicker(track); }}
                                className="w-full px-3 py-1.5 text-left text-[11px] text-text-secondary hover:bg-[#161616] transition-colors"
                              >
                                Add to playlist
                              </button>
                              <button
                                onClick={() => handleQueueTrackRemove(i)}
                                className="w-full px-3 py-1.5 text-left text-[11px] text-danger hover:bg-[#161616] transition-colors"
                              >
                                Remove
                              </button>
                              <button
                                disabled
                                className="w-full px-3 py-1.5 text-left text-[11px] text-text-muted opacity-60 cursor-not-allowed"
                              >
                                Report (soon)
                              </button>
                            </div>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
        )}

        {playlistPickerOpen && (
          <div className="fixed inset-0 z-40 bg-black/60 flex items-center justify-center p-4">
            <div className="w-full max-w-md bg-[#0d0d0d] border border-[#2a2a2a] p-4">
              <h3 className="text-sm font-semibold text-text-primary">Add To Playlist</h3>
              <p className="text-xs text-text-muted mt-1 truncate">
                {playlistTrack ? `Track: ${playlistTrack.title}` : 'Select a playlist'}
              </p>

              {playlistPickerLoading ? (
                <div className="flex items-center gap-2 text-xs text-text-muted mt-4">
                  <Disc size={12} className="animate-spin" />
                  Loading playlists...
                </div>
              ) : (
                <>
                  <div className="mt-4">
                    <label className="block text-[10px] font-mono text-text-muted uppercase tracking-wider mb-1.5">
                      Playlist
                    </label>
                    <select
                      value={selectedPlaylistId}
                      onChange={e => setSelectedPlaylistId(e.target.value)}
                      className="w-full bg-[#111] border border-[#2a2a2a] text-text-primary text-sm px-3 py-2 focus:outline-none focus:border-accent"
                      disabled={playlistOptions.length === 0 || playlistPickerSaving}
                    >
                      {playlistOptions.length === 0 ? (
                        <option value="">No playlists available</option>
                      ) : (
                        playlistOptions.map(pl => (
                          <option key={pl.id} value={pl.id}>
                            {pl.name} ({pl.track_count} tracks)
                          </option>
                        ))
                      )}
                    </select>
                  </div>

                  {playlistPickerError && (
                    <p className="text-xs text-danger mt-3">{playlistPickerError}</p>
                  )}
                </>
              )}

              <div className="mt-4 flex items-center justify-end gap-2">
                <button
                  onClick={closePlaylistPicker}
                  disabled={playlistPickerSaving}
                  className="px-3 py-1.5 text-xs border border-[#333] text-text-muted hover:text-text-primary transition-colors disabled:opacity-40"
                >
                  Cancel
                </button>
                <button
                  onClick={() => navigate({ to: '/library/playlists' })}
                  disabled={playlistPickerSaving}
                  className="px-3 py-1.5 text-xs border border-[#333] text-text-muted hover:text-text-primary transition-colors disabled:opacity-40"
                >
                  Open Playlists
                </button>
                <button
                  onClick={confirmAddToPlaylist}
                  disabled={playlistPickerLoading || playlistPickerSaving || !playlistTrack || !selectedPlaylistId}
                  className="px-3 py-1.5 text-xs bg-accent text-black hover:bg-accent/80 transition-colors disabled:opacity-40"
                >
                  {playlistPickerSaving ? 'Adding...' : 'Add'}
                </button>
              </div>
            </div>
          </div>
        )}

      </div>
    </div>
  );
}

