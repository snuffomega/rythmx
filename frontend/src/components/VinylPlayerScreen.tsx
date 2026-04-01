/**
 * VinylPlayerScreen — immersive vinyl jacket aesthetic full-screen player.
 *
 * Inspired by the physical experience of holding an LP record:
 *   - Large centered album sleeve art with depth, shadow, and sleeve-border treatment
 *   - Three-cover stage: smaller Previous | large Current | smaller Upcoming
 *     The side covers are tappable to skip tracks and tilt back in 3D
 *   - Header: Minimize (left) | centered song title + artist | Settings + Standard-View (right)
 *   - Progress bar directly under the main art with click-to-seek
 *   - Transport bar: Shuffle | Prev | Play/Pause | Next | Repeat
 *
 * Architecture:
 *   - Pure consumer of usePlayerStore — zero audio logic lives here
 *   - onPlayPause / onMinimize / onSeek are threaded from __root.tsx,
 *     which owns the single useAudioEngine instance (same pattern as FullPagePlayer)
 *   - All queue mutations call store actions directly
 *
 * Navigation:
 *   - Enter : FullPagePlayer Disc icon → store.showVinyl()
 *   - Exit (standard view) : LayoutList icon → store.expand()
 *   - Exit (mini bar)      : Minimize2 → onMinimize()
 */
import { useRef } from 'react';
import {
  Play, Pause, SkipBack, SkipForward,
  Shuffle, Repeat, Minimize2, LayoutList, Settings, Disc,
} from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { usePlayerStore } from '../stores/usePlayerStore';
import { useImage } from '../hooks/useImage';

interface VinylPlayerScreenProps {
  isPlaying: boolean;
  onPlayPause: () => void;
  onMinimize: () => void;
  onSeek: (seconds: number) => void;
}

// ── Artwork helpers ──────────────────────────────────────────────────────────

interface ArtCardProps {
  src: string | null;
  alt: string;
  size: 'sm' | 'lg';
}

function ArtCard({ src, alt, size }: ArtCardProps) {
  const dim = size === 'lg'
    ? 'w-full h-full'
    : 'w-full h-full';

  if (src) {
    return (
      <img
        src={src}
        alt={alt}
        className={`${dim} object-cover`}
        draggable={false}
      />
    );
  }
  return (
    <div className={`${dim} bg-[#111] flex items-center justify-center`}>
      <Disc
        size={size === 'lg' ? 64 : 28}
        className="text-[#2a2a2a]"
      />
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────

export function VinylPlayerScreen({
  isPlaying,
  onPlayPause,
  onMinimize,
  onSeek,
}: VinylPlayerScreenProps) {
  const navigate = useNavigate();
  const progressRef = useRef<HTMLDivElement>(null);

  const {
    currentTrack,
    queue,
    queueIndex,
    position,
    duration,
    formattedPosition,
    formattedDuration,
    shuffle,
    repeat,
    nextTrack,
    prevTrack,
    toggleShuffle,
    toggleRepeat,
    expand,
  } = usePlayerStore();

  // ── Adjacent track items ─────────────────────────────────────────────────
  const prevTrackItem = queueIndex > 0 ? queue[queueIndex - 1] : null;
  const nextTrackItem = (() => {
    if (shuffle) return null; // indeterminate
    if (repeat && queueIndex === queue.length - 1) return queue[0] ?? null;
    return queue[queueIndex + 1] ?? null;
  })();

  // ── Image resolution for all three slots ────────────────────────────────
  // All three hooks are called unconditionally (Rules of Hooks).
  // When a slot is empty, empty strings are passed and useImage returns null.
  const prevResolved  = useImage('album', prevTrackItem?.album  ?? '', prevTrackItem?.artist  ?? '');
  const currResolved  = useImage('album', currentTrack?.album   ?? '', currentTrack?.artist   ?? '');
  const nextResolved  = useImage('album', nextTrackItem?.album  ?? '', nextTrackItem?.artist  ?? '');

  const prevSrc = prevTrackItem?.thumb_url ?? prevResolved;
  const currSrc = currentTrack?.thumb_url  ?? currResolved;
  const nextSrc = nextTrackItem?.thumb_url ?? nextResolved;

  // ── Progress ─────────────────────────────────────────────────────────────
  const progressPct = duration > 0 ? (position / duration) * 100 : 0;

  function handleProgressClick(e: React.MouseEvent<HTMLDivElement>) {
    if (!progressRef.current || duration <= 0) return;
    const rect = progressRef.current.getBoundingClientRect();
    const pct  = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    onSeek(pct * duration);
  }

  // ── Layout ────────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full min-h-0 flex-col bg-base overflow-hidden">

      {/* ── Header ──────────────────────────────────────────────────────── */}
      {/* 3-column grid so the center title is always truly centered        */}
      <div className="grid grid-cols-[44px_1fr_auto] items-center px-6 py-5 flex-shrink-0 gap-2">

        {/* Left: minimize */}
        <button
          onClick={onMinimize}
          className="text-text-muted hover:text-text-secondary transition-colors justify-self-start"
          aria-label="Minimize player"
        >
          <Minimize2 size={17} />
        </button>

        {/* Center: song title + artist — truncated */}
        <div className="text-center min-w-0 px-2">
          <p className="text-[15px] font-semibold text-text-primary truncate leading-tight">
            {currentTrack?.title ?? 'Nothing playing'}
          </p>
          <p className="font-mono text-[12px] text-text-secondary truncate leading-tight mt-0.5">
            {currentTrack?.artist ?? '—'}
          </p>
        </div>

        {/* Right: settings + standard-view toggle */}
        <div className="flex items-center gap-3 justify-self-end">
          <button
            onClick={() => navigate({ to: '/settings' })}
            className="text-text-muted hover:text-text-secondary transition-colors"
            aria-label="Settings"
            title="Settings"
          >
            <Settings size={15} />
          </button>
          <button
            onClick={expand}
            className="text-text-muted hover:text-text-secondary transition-colors"
            aria-label="Standard view"
            title="Standard view"
          >
            <LayoutList size={16} />
          </button>
        </div>
      </div>

      {/* ── Three-cover stage ─────────────────────────────────────────────── */}
      {/* The outer div sets the 3D perspective context. The inner flex row   */}
      {/* holds prev (small, angled back) | current (large, forward) | next.  */}
      <div
        className="flex-1 flex items-center justify-center min-h-0 px-6"
        style={{ perspective: '1100px' }}
      >
        <div className="flex items-center justify-center [transform-style:preserve-3d] gap-4">

          {/* ── Previous cover ────────────────────────────────────────── */}
          <button
            onClick={() => prevTrackItem && prevTrack()}
            disabled={!prevTrackItem}
            aria-label="Previous track"
            className="flex-shrink-0 rounded-lg overflow-hidden transition-opacity duration-200 focus:outline-none disabled:cursor-default"
            style={{
              width:     'clamp(72px, 11vw, 110px)',
              height:    'clamp(72px, 11vw, 110px)',
              transform: 'rotateY(32deg) translateZ(-55px)',
              opacity:   prevTrackItem ? 0.48 : 0.14,
              border:    '1.5px solid rgba(255,255,255,0.07)',
              boxShadow: '0 8px 24px rgba(0,0,0,0.55)',
            }}
          >
            <ArtCard src={prevSrc} alt="previous track" size="sm" />
          </button>

          {/* ── Current main art — LP jacket treatment ────────────────── */}
          <div
            className="flex-shrink-0 rounded-xl overflow-hidden relative"
            style={{
              width:     'clamp(220px, 40vw, 380px)',
              height:    'clamp(220px, 40vw, 380px)',
              border:    '1.5px solid rgba(255,255,255,0.06)',
              // Deep LP sleeve shadow — two layers of shadow for richness
              boxShadow: [
                '0 32px 80px rgba(0,0,0,0.80)',
                '0 8px 24px rgba(0,0,0,0.60)',
                'inset 0 1px 0 rgba(255,255,255,0.04)',
              ].join(', '),
            }}
          >
            <ArtCard src={currSrc} alt={currentTrack?.title ?? 'album art'} size="lg" />

            {/* Subtle top-edge sleeve highlight — mimics printed LP jacket sheen */}
            <div
              className="absolute inset-x-0 top-0 h-px pointer-events-none"
              style={{ background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.08) 40%, rgba(255,255,255,0.12) 50%, rgba(255,255,255,0.08) 60%, transparent)' }}
            />
          </div>

          {/* ── Next cover ────────────────────────────────────────────── */}
          <button
            onClick={() => nextTrackItem && nextTrack()}
            disabled={!nextTrackItem}
            aria-label="Next track"
            className="flex-shrink-0 rounded-lg overflow-hidden transition-opacity duration-200 focus:outline-none disabled:cursor-default"
            style={{
              width:     'clamp(72px, 11vw, 110px)',
              height:    'clamp(72px, 11vw, 110px)',
              transform: 'rotateY(-32deg) translateZ(-55px)',
              opacity:   nextTrackItem ? 0.48 : 0.14,
              border:    '1.5px solid rgba(255,255,255,0.07)',
              boxShadow: '0 8px 24px rgba(0,0,0,0.55)',
            }}
          >
            <ArtCard src={nextSrc} alt="next track" size="sm" />
          </button>

        </div>
      </div>

      {/* ── Progress bar + time ──────────────────────────────────────────── */}
      <div className="flex-shrink-0 px-10 pt-6 pb-2 max-w-[540px] mx-auto w-full">
        <div className="flex items-center gap-3">

          <span className="font-mono text-[11px] text-text-muted tabular-nums w-9 text-right flex-shrink-0">
            {formattedPosition}
          </span>

          {/* Clickable progress track */}
          <div
            ref={progressRef}
            onClick={handleProgressClick}
            className="flex-1 h-[3px] bg-[#1e1e1e] rounded-full relative cursor-pointer group"
          >
            {/* Filled portion */}
            <div
              className="absolute top-0 left-0 h-full bg-accent rounded-full pointer-events-none"
              style={{ width: `${progressPct}%` }}
            />
            {/* Playhead — visible on hover */}
            <div
              className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-accent rounded-full opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
              style={{ left: `${progressPct}%`, marginLeft: '-6px' }}
            />
          </div>

          <span className="font-mono text-[11px] text-text-muted tabular-nums w-9 flex-shrink-0">
            {formattedDuration}
          </span>
        </div>
      </div>

      {/* ── Transport controls ────────────────────────────────────────────── */}
      <div className="flex-shrink-0 flex items-center justify-center gap-8 pb-8 pt-5">

        {/* Shuffle */}
        <button
          onClick={toggleShuffle}
          className={`transition-colors ${shuffle ? 'text-accent' : 'text-text-muted hover:text-text-secondary'}`}
          aria-label={shuffle ? 'Shuffle on' : 'Shuffle off'}
          title="Shuffle"
        >
          <Shuffle size={19} />
        </button>

        {/* Previous */}
        <button
          onClick={prevTrack}
          disabled={!currentTrack}
          className="text-text-secondary hover:text-text-primary transition-colors disabled:opacity-30"
          aria-label="Previous track"
        >
          <SkipBack size={26} />
        </button>

        {/* Play / Pause — primary action, prominent size */}
        <button
          onClick={onPlayPause}
          disabled={!currentTrack}
          aria-label={isPlaying ? 'Pause' : 'Play'}
          className="w-[62px] h-[62px] rounded-full bg-accent hover:bg-accent/85 flex items-center justify-center transition-colors disabled:opacity-30 flex-shrink-0"
        >
          {isPlaying
            ? <Pause size={24} className="text-black" />
            : <Play  size={24} className="text-black ml-0.5" />}
        </button>

        {/* Next */}
        <button
          onClick={nextTrack}
          disabled={!currentTrack}
          className="text-text-secondary hover:text-text-primary transition-colors disabled:opacity-30"
          aria-label="Next track"
        >
          <SkipForward size={26} />
        </button>

        {/* Repeat */}
        <button
          onClick={toggleRepeat}
          className={`transition-colors ${repeat ? 'text-accent' : 'text-text-muted hover:text-text-secondary'}`}
          aria-label={repeat ? 'Repeat on' : 'Repeat off'}
          title="Repeat"
        >
          <Repeat size={19} />
        </button>

      </div>

    </div>
  );
}
