/**
 * PlayerBar - mini player bar fixed at the bottom of the app layout.
 */
import {
  Play, Pause, SkipBack, SkipForward,
  Volume2, Repeat, Repeat1, Shuffle, List, Maximize2, Star, X, MoreHorizontal, Trash2, GripVertical,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { usePlayerStore } from '../stores/usePlayerStore';
import { TrackArt } from './common';
import { libraryBrowseApi } from '../services/api';

interface PlayerBarProps {
  isPlaying: boolean;
  onPlayPause: () => void;
  onExpand: () => void;
  onSeek: (seconds: number) => void;
  onVolumeChange: (vol: number) => void;
}

function formatTrackDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '--:--';
  const total = Math.floor(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function PlayerBar({ isPlaying, onPlayPause, onExpand, onSeek, onVolumeChange }: PlayerBarProps) {
  const navigate = useNavigate();
  const [artistNavLoading, setArtistNavLoading] = useState(false);
  const [ratingByTrack, setRatingByTrack] = useState<Record<string, number>>({});
  const [hoverRating, setHoverRating] = useState(0);
  const [showQueue, setShowQueue] = useState(false);
  const [queueMenuIndex, setQueueMenuIndex] = useState<number | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dropIndex, setDropIndex] = useState<number | null>(null);
  const {
    currentTrack,
    queue,
    queueIndex,
    formattedPosition,
    formattedDuration,
    position,
    duration,
    volume,
    shuffle,
    repeatMode,
    nextTrack,
    prevTrack,
    toggleShuffle,
    toggleRepeat,
  } = usePlayerStore();

  const progressRef = useRef<HTMLDivElement>(null);
  const volumeRef = useRef<HTMLDivElement>(null);
  const queueButtonRef = useRef<HTMLButtonElement>(null);
  const queuePanelRef = useRef<HTMLDivElement>(null);

  const progressPct = duration > 0 ? (position / duration) * 100 : 0;
  const effectiveDuration = duration > 0 ? duration : (currentTrack?.duration ?? 0);
  const currentRating = currentTrack ? ratingByTrack[currentTrack.id] ?? 0 : 0;
  const activeRating = hoverRating || currentRating;

  function handleProgressClick(e: React.MouseEvent<HTMLDivElement>) {
    if (!progressRef.current || effectiveDuration <= 0) return;
    const rect = progressRef.current.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    onSeek(pct * effectiveDuration);
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

  function handleVolumeMouseDown(e: React.MouseEvent<HTMLDivElement>) {
    if (!volumeRef.current) return;
    const rect = volumeRef.current.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    onVolumeChange(pct);
    const onMove = (event: MouseEvent) => {
      if (!volumeRef.current) return;
      const moveRect = volumeRef.current.getBoundingClientRect();
      const movePct = Math.max(0, Math.min(1, (event.clientX - moveRect.left) / moveRect.width));
      onVolumeChange(movePct);
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }

  async function handleArtistClick() {
    const artistName = currentTrack?.artist?.trim();
    if (!artistName || artistNavLoading) return;
    setArtistNavLoading(true);
    try {
      const res = await libraryBrowseApi.getArtists({ q: artistName, per_page: 25 });
      const exact = res.artists.find((a) => a.name.toLowerCase() === artistName.toLowerCase()) ?? res.artists[0];
      if (exact) {
        navigate({ to: '/library/artist/$id', params: { id: exact.id } });
      } else {
        navigate({ to: '/library' });
      }
    } finally {
      setArtistNavLoading(false);
    }
  }

  async function handleRate(star: number) {
    if (!currentTrack) return;
    const next = star === currentRating ? 0 : star;
    setRatingByTrack((prev) => ({ ...prev, [currentTrack.id]: next }));
    try {
      await libraryBrowseApi.rateTrack(currentTrack.id, next * 2);
    } catch {
      // Keep mini-bar interaction optimistic and non-blocking.
    }
  }

  function handleExpand() {
    setShowQueue(false);
    setQueueMenuIndex(null);
    clearQueueDragState();
    onExpand();
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

  useEffect(() => {
    if (!showQueue) return;
    const onMouseDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (queuePanelRef.current?.contains(target)) return;
      if (queueButtonRef.current?.contains(target)) return;
      setShowQueue(false);
      setQueueMenuIndex(null);
      clearQueueDragState();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setShowQueue(false);
        setQueueMenuIndex(null);
        clearQueueDragState();
      }
    };
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [showQueue]);

  return (
    <div className="fixed bottom-0 left-0 right-0 z-30 h-[96px] bg-[#0a0a0a] border-t border-[#1a1a1a]">

      {/* ── Seek bar — top edge ─────────────────────────────────────────── */}
      <div
        ref={progressRef}
        onMouseDown={handleProgressMouseDown}
        onClick={handleProgressClick}
        className="absolute top-0 left-0 right-0 h-4 cursor-pointer group select-none z-10"
      >
        <div className="absolute top-0 left-0 right-0 h-[3px] bg-[#1a1a1a]">
          <div className="h-full bg-accent" style={{ width: `${progressPct}%` }} />
        </div>
        <div
          className="absolute w-3 h-3 bg-accent rounded-full opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
          style={{ left: `${progressPct}%`, marginLeft: '-6px', top: '1.5px', transform: 'translateY(-50%)' }}
        />
      </div>

      <div className="h-full pl-[72px] pr-2 relative flex items-center">

        {/* ── Left: album art + track info ─────────────────────────────── */}
        <div className="flex items-center gap-4 min-w-0 flex-shrink-0" style={{ width: 'clamp(200px, 26%, 300px)' }}>
          <button
            onClick={handleExpand}
            disabled={!currentTrack}
            className="flex-shrink-0 rounded-sm transition-all hover:brightness-110 active:scale-95 disabled:opacity-60 disabled:cursor-default"
            aria-label="Open large player"
            title="Open large player"
          >
            <TrackArt
              thumbUrl={currentTrack?.thumb_url ?? null}
              thumbHash={currentTrack?.thumb_hash ?? null}
              title={currentTrack?.album ?? ''}
              artist={currentTrack?.artist ?? ''}
              size="sm"
            />
          </button>
          <div className="min-w-0 flex-1">
            <p className="text-[15px] text-text-primary truncate leading-tight">
              {currentTrack?.title ?? 'Nothing playing'}
            </p>
            <button
              onClick={handleArtistClick}
              disabled={!currentTrack?.artist || artistNavLoading}
              className="font-mono text-[13px] text-accent truncate leading-tight mt-0.5 text-left max-w-full disabled:cursor-default disabled:opacity-60"
              title={currentTrack?.artist ? `Open ${currentTrack.artist}` : undefined}
            >
              {currentTrack?.artist ?? '-'}
            </button>
            <p className="font-mono text-[10px] text-text-muted tabular-nums mt-0.5 leading-none">
              {formattedPosition}
              {currentTrack && <span className="text-[#333]"> / {formattedDuration}</span>}
            </p>
          </div>
        </div>

        {/* ── Center: transport ────────────────────────────────────────── */}
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 flex items-center gap-4">
          <button
            onClick={toggleShuffle}
            className={`transition-colors ${shuffle ? 'text-accent' : 'text-text-muted hover:text-text-secondary'}`}
            aria-label="Shuffle"
          >
            <Shuffle size={17} />
          </button>
          <button
            onClick={prevTrack}
            className="text-text-secondary hover:text-text-primary transition-colors"
            aria-label="Previous"
            disabled={!currentTrack}
          >
            <SkipBack size={20} />
          </button>
          <button
            onClick={onPlayPause}
            aria-label={isPlaying ? 'Pause' : 'Play'}
            disabled={!currentTrack}
            className="w-11 h-11 rounded-full bg-accent hover:bg-accent/80 flex items-center justify-center transition-colors disabled:opacity-40"
          >
            {isPlaying
              ? <Pause size={18} className="text-black" />
              : <Play size={18} className="text-black ml-0.5" />}
          </button>
          <button
            onClick={nextTrack}
            className="text-text-secondary hover:text-text-primary transition-colors"
            aria-label="Next"
            disabled={!currentTrack}
          >
            <SkipForward size={20} />
          </button>
          <button
            onClick={toggleRepeat}
            className={`transition-colors ${repeatMode !== 'off' ? 'text-accent' : 'text-text-muted hover:text-text-secondary'}`}
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
          >
            {repeatMode === 'one' ? <Repeat1 size={17} /> : <Repeat size={17} />}
          </button>
        </div>

        {/* ── Right: volume + stars + expand + queue ───────────────────── */}
        <div className="ml-auto relative flex items-center gap-3 flex-shrink-0">

          {/* Volume row + star rating stacked */}
          <div className="flex flex-col items-center gap-1.5">
            <div className="flex items-center gap-2">
              <Volume2 size={15} className="text-text-secondary flex-shrink-0" />
              <div
                ref={volumeRef}
                onMouseDown={handleVolumeMouseDown}
                className="w-24 h-[4px] bg-[#1a1a1a] rounded-full relative cursor-pointer group"
              >
                <div
                  className="absolute top-0 left-0 h-full bg-accent rounded-full pointer-events-none"
                  style={{ width: `${volume * 100}%` }}
                />
                <div
                  className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-accent rounded-full opacity-70 group-hover:opacity-100 transition-opacity pointer-events-none"
                  style={{ left: `${volume * 100}%`, marginLeft: '-5px' }}
                />
              </div>
            </div>
            <div className="flex items-center gap-1" onMouseLeave={() => setHoverRating(0)}>
              {[1, 2, 3, 4, 5].map((star) => (
                <button
                  key={star}
                  onMouseEnter={() => setHoverRating(star)}
                  onClick={() => handleRate(star)}
                  className="transition-colors"
                  aria-label={`Rate ${star} stars`}
                >
                  <Star
                    size={13}
                    className={star <= activeRating ? 'text-accent' : 'text-[#2a2a2a]'}
                    fill={star <= activeRating ? 'currentColor' : 'none'}
                  />
                </button>
              ))}
            </div>
          </div>

          {/* Expand */}
          <button
            onClick={handleExpand}
            className="text-text-muted hover:text-text-secondary transition-colors"
            aria-label="Open large player"
            title="Open large player"
          >
            <Maximize2 size={15} />
          </button>

          {/* Queue button — mirrors album art (w-16 h-16) */}
          <button
            ref={queueButtonRef}
            onClick={() => {
              setQueueMenuIndex(null);
              setShowQueue((open) => {
                if (open) clearQueueDragState();
                return !open;
              });
            }}
            className={`relative w-16 h-16 flex-shrink-0 flex items-center justify-center rounded-sm border transition-colors ${
              showQueue
                ? 'border-accent/60 bg-[#141414] text-accent'
                : 'border-[#1e1e1e] bg-[#111] text-text-muted hover:border-[#2a2a2a] hover:text-text-secondary'
            }`}
            aria-label="Queue"
            title="Queue"
          >
            <List size={20} />
            {queue.length > 0 && (
              <span className="absolute -top-1.5 -right-1.5 min-w-[18px] h-[18px] rounded-full bg-accent text-black text-[8px] font-bold flex items-center justify-center leading-none tabular-nums px-1">
                {queue.length > 99 ? '99+' : queue.length}
              </span>
            )}
          </button>

          {showQueue && (
            <div
              ref={queuePanelRef}
              className="absolute bottom-full right-0 mb-3 w-[min(90vw,460px)] border border-[#1e1e1e] rounded-xl overflow-hidden bg-[#0a0a0a] shadow-2xl z-40
                         transition-[transform,opacity,box-shadow] duration-200 ease-out"
            >
              <div className="flex items-center justify-between px-4 py-3 border-b border-[#1a1a1a]">
                <span className="font-mono text-[11px] text-text-muted uppercase tracking-widest">
                  Queue - {queue.length} track{queue.length !== 1 ? 's' : ''}
                </span>
                <div className="flex items-center gap-2">
                  {queue.length > 0 && (
                    <button
                      onClick={() => { usePlayerStore.getState().clearQueue(); setQueueMenuIndex(null); clearQueueDragState(); }}
                      className="text-[10px] font-mono text-text-muted hover:text-text-secondary transition-colors"
                    >
                      Clear
                    </button>
                  )}
                  <button
                    onClick={() => { setShowQueue(false); setQueueMenuIndex(null); clearQueueDragState(); }}
                    aria-label="Close queue"
                    className="p-1 text-text-muted hover:text-text-primary transition-colors"
                  >
                    <X size={14} />
                  </button>
                </div>
              </div>
              <div className="max-h-[36vh] overflow-y-auto">
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
                          className={`relative group px-4 py-2.5 flex items-center gap-2.5 transition-colors ${
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
                            onClick={() => { usePlayerStore.getState().playAt(i); setQueueMenuIndex(null); setShowQueue(false); clearQueueDragState(); }}
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
                                onClick={() => { usePlayerStore.getState().playAt(i); setQueueMenuIndex(null); setShowQueue(false); clearQueueDragState(); }}
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
      </div>
    </div>
  );
}
