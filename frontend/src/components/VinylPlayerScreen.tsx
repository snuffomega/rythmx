/**
 * VinylPlayerScreen — full-screen alternate player layout.
 *
 * This is a typography-first, vinyl-aesthetic player view. It surfaces
 * queue context (prev / current / next track) and exposes all standard
 * transport controls. Album art and seek bar are intentionally omitted
 * from this barebones scaffold — add them here when the vinyl aesthetic
 * is fleshed out.
 *
 * Architecture:
 *   - Pure consumer of usePlayerStore — zero audio logic lives here.
 *   - onPlayPause / onMinimize are threaded from __root.tsx, which owns
 *     the single useAudioEngine instance (same pattern as FullPagePlayer).
 *   - All queue mutations (next, prev, shuffle, repeat) call the store
 *     directly — they don't touch the audio element.
 *
 * Navigation:
 *   - Enter : FullPagePlayer's vinyl toggle button → store.showVinyl()
 *   - Exit (standard view) : "Standard View" button → store.expand()
 *   - Exit (mini bar)      : Minimize button → onMinimize()
 */
import { Play, Pause, SkipBack, SkipForward, Shuffle, Repeat, Minimize2, LayoutList } from 'lucide-react';
import { usePlayerStore } from '../stores/usePlayerStore';

interface VinylPlayerScreenProps {
  isPlaying: boolean;
  onPlayPause: () => void;
  onMinimize: () => void;
}

export function VinylPlayerScreen({ isPlaying, onPlayPause, onMinimize }: VinylPlayerScreenProps) {
  const {
    currentTrack,
    queue,
    queueIndex,
    shuffle,
    repeat,
    nextTrack,
    prevTrack,
    toggleShuffle,
    toggleRepeat,
    expand,
  } = usePlayerStore();

  // ── Adjacent track labels ──────────────────────────────────────────────
  // When shuffle is active we can't predict the next pick, so we show '?'.
  // When repeat is on and we're at the end of the queue, next wraps to [0].
  const prevTrackItem = queueIndex > 0 ? queue[queueIndex - 1] : null;
  const nextTrackItem = (() => {
    if (shuffle) return null; // random — indeterminate
    if (repeat && queueIndex === queue.length - 1) return queue[0] ?? null; // wrap
    return queue[queueIndex + 1] ?? null;
  })();

  return (
    <div className="flex h-full min-h-0 flex-col items-center justify-between py-10 px-8 bg-base">

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="w-full flex items-center justify-between flex-shrink-0">
        <span className="font-mono text-[11px] text-text-muted uppercase tracking-widest">
          Vinyl View
        </span>
        <div className="flex items-center gap-4">
          {/* Return to the standard full-page player */}
          <button
            onClick={expand}
            className="text-text-muted hover:text-text-secondary transition-colors"
            aria-label="Standard view"
            title="Standard view"
          >
            <LayoutList size={16} />
          </button>
          {/* Collapse to the mini bar */}
          <button
            onClick={onMinimize}
            className="text-text-muted hover:text-text-secondary transition-colors"
            aria-label="Minimize player"
            title="Minimize"
          >
            <Minimize2 size={16} />
          </button>
        </div>
      </div>

      {/* ── Queue context: prev → current → next ────────────────────────── */}
      <div className="w-full max-w-[560px] mx-auto flex flex-col items-center gap-5 text-center">

        {/* Previous track label */}
        <p className="font-mono text-[12px] text-text-muted truncate max-w-full leading-tight select-none">
          {prevTrackItem ? `↑  ${prevTrackItem.title}` : '—'}
        </p>

        {/* Current track — primary focus */}
        <div className="flex flex-col items-center gap-1.5">
          <p className="text-[28px] font-semibold text-text-primary leading-tight truncate max-w-full">
            {currentTrack?.title ?? 'Nothing playing'}
          </p>
          <p className="font-mono text-[15px] text-text-secondary leading-tight">
            {currentTrack?.artist ?? '—'}
          </p>
          <p className="font-mono text-[13px] text-text-muted leading-tight">
            {currentTrack?.album ?? ''}
          </p>
          {/* Queue position */}
          {queue.length > 0 && (
            <p className="font-mono text-[11px] text-text-muted mt-1 tabular-nums">
              {queueIndex + 1} / {queue.length}
            </p>
          )}
        </div>

        {/* Next track label */}
        <p className="font-mono text-[12px] text-text-muted truncate max-w-full leading-tight select-none">
          {shuffle
            ? '↓  ?'
            : nextTrackItem
              ? `↓  ${nextTrackItem.title}`
              : '—'}
        </p>
      </div>

      {/* ── Transport controls ────────────────────────────────────────────── */}
      <div className="flex items-center gap-8 flex-shrink-0">

        {/* Shuffle — accent when active */}
        <button
          onClick={toggleShuffle}
          className={`transition-colors ${shuffle ? 'text-accent' : 'text-text-muted hover:text-text-secondary'}`}
          aria-label={shuffle ? 'Shuffle on' : 'Shuffle off'}
          title="Shuffle"
        >
          <Shuffle size={18} />
        </button>

        {/* Previous track */}
        <button
          onClick={prevTrack}
          className="text-text-secondary hover:text-text-primary transition-colors disabled:opacity-40"
          aria-label="Previous track"
          disabled={!currentTrack}
        >
          <SkipBack size={24} />
        </button>

        {/* Play / Pause — primary action */}
        <button
          onClick={onPlayPause}
          aria-label={isPlaying ? 'Pause' : 'Play'}
          disabled={!currentTrack}
          className="w-16 h-16 rounded-full bg-accent hover:bg-accent/80 flex items-center justify-center transition-colors disabled:opacity-40"
        >
          {isPlaying
            ? <Pause size={24} className="text-black" />
            : <Play  size={24} className="text-black ml-0.5" />}
        </button>

        {/* Next track */}
        <button
          onClick={nextTrack}
          className="text-text-secondary hover:text-text-primary transition-colors disabled:opacity-40"
          aria-label="Next track"
          disabled={!currentTrack}
        >
          <SkipForward size={24} />
        </button>

        {/* Repeat — accent when active */}
        <button
          onClick={toggleRepeat}
          className={`transition-colors ${repeat ? 'text-accent' : 'text-text-muted hover:text-text-secondary'}`}
          aria-label={repeat ? 'Repeat on' : 'Repeat off'}
          title="Repeat"
        >
          <Repeat size={18} />
        </button>
      </div>

    </div>
  );
}
