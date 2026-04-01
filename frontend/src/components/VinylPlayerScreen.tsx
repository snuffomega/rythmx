/**
 * VinylPlayerScreen — immersive vinyl jacket aesthetic full-screen player.
 *
 * Layout (top → bottom):
 *   1. Minimal header: ChevronDown minimize (left) + LayoutList standard-view (right)
 *   2. Three-cover carousel stage (flex-1):
 *        prev cover (rotateY+28°, scaled back) | main cover (LP sleeve) | next cover (rotateY-28°)
 *        Side covers are tappable to skip tracks.
 *   3. Track identity block (centered below art):
 *        Song title · Artist · Album · decorative waveform · audio quality badge
 *   4. Bottom control bar:
 *        Left  : album thumb + track name + album name
 *        Center: [Shuffle Prev Play Next Repeat] ABOVE [time ──●── time]
 *        Right : [★★★★☆] [♥] [lyrics] [live] [settings] [queue toggle w/ badge]
 *
 * Artwork update bug fix:
 *   useImage is called *inside* ArtCard, and the parent passes a `key` based on
 *   track.id. When the track changes React fully unmounts + remounts ArtCard,
 *   resetting the useState inside useImage to the correct initial cache value
 *   and triggering a fresh fetch if needed.
 *
 * Tactile feedback:
 *   All interactive buttons carry `active:scale-90 transition-all` so taps
 *   produce a physical scale-down spring response.
 *
 * Architecture:
 *   - Pure consumer of usePlayerStore — zero audio logic lives here.
 *   - onPlayPause / onMinimize / onSeek are owned by __root.tsx → useAudioEngine.
 *   - starRating / liked / showQueue are local UI state — no backend today.
 *
 * Navigation:
 *   - Enter  : FullPagePlayer Disc icon → store.showVinyl()
 *   - Exit A : ChevronDown → onMinimize() (mini bar)
 *   - Exit B : LayoutList  → store.expand() (standard full-page player)
 */
import { useRef, useState } from 'react';
import {
  Play, Pause, SkipBack, SkipForward,
  Shuffle, Repeat, LayoutList, Settings,
  Disc, Star, Heart, ListMusic, Mic2,
  ChevronDown, List,
} from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { usePlayerStore, type PlayerTrack } from '../stores/usePlayerStore';
import { useImage } from '../hooks/useImage';
import { AudioQualityBadge } from './common';

// ── Props ────────────────────────────────────────────────────────────────────

interface VinylPlayerScreenProps {
  isPlaying: boolean;
  onPlayPause: () => void;
  onMinimize: () => void;
  onSeek: (seconds: number) => void;
}

// ── Decorative waveform ──────────────────────────────────────────────────────
// Static bar heights — purely cosmetic; evokes audio waveform without Web Audio API.
const WAVE_HEIGHTS = [4, 7, 11, 15, 9, 18, 13, 7, 20, 12, 16, 9, 5, 14, 10, 7, 17, 12, 8, 18, 13, 9, 4, 14, 10, 7, 18, 12, 6, 15];

function Waveform() {
  return (
    <div className="flex items-center justify-center gap-[2.5px]" style={{ height: '22px' }}>
      {WAVE_HEIGHTS.map((h, i) => (
        <div
          key={i}
          className="rounded-full flex-shrink-0"
          style={{
            width: '2.5px',
            height: `${h}px`,
            background: 'rgba(212,245,60,0.22)',
          }}
        />
      ))}
    </div>
  );
}

// ── Album art card ───────────────────────────────────────────────────────────
// useImage lives HERE (not in parent) so that passing key={track?.id} from the
// parent causes a full remount, resetting useState and triggering a fresh
// image resolution whenever the track changes.
function ArtCard({ track, size }: { track: PlayerTrack | null; size: 'sm' | 'lg' }) {
  const resolved = useImage('album', track?.album ?? '', track?.artist ?? '');
  const src = track?.thumb_url ?? resolved ?? null;

  if (src) {
    return (
      <img
        src={src}
        alt={track?.album ?? 'album art'}
        className="w-full h-full object-cover"
        draggable={false}
      />
    );
  }
  return (
    <div className="w-full h-full bg-[#0f0f0f] flex items-center justify-center">
      <Disc size={size === 'lg' ? 56 : 22} className="text-[#252525]" />
    </div>
  );
}

// ── Star rating (local UI state only — no backend persistence yet) ────────────
function StarRating({ rating, onChange }: { rating: number; onChange: (n: number) => void }) {
  const [hover, setHover] = useState(0);
  return (
    <div className="flex items-center gap-[2px]">
      {[1, 2, 3, 4, 5].map(n => (
        <button
          key={n}
          onClick={() => onChange(n === rating ? 0 : n)}
          onMouseEnter={() => setHover(n)}
          onMouseLeave={() => setHover(0)}
          aria-label={`Rate ${n} star${n !== 1 ? 's' : ''}`}
          className="active:scale-90 transition-transform"
        >
          <Star
            size={13}
            className={`transition-colors ${
              n <= (hover || rating) ? 'text-accent' : 'text-[#303030]'
            }`}
            fill={n <= (hover || rating) ? 'currentColor' : 'none'}
          />
        </button>
      ))}
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
  const navigate    = useNavigate();
  const progressRef = useRef<HTMLDivElement>(null);

  // Local UI state
  const [starRating, setStarRating] = useState(0);
  const [liked,      setLiked]      = useState(false);
  const [showQueue,  setShowQueue]  = useState(false);

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
    playAt,
    toggleShuffle,
    toggleRepeat,
    expand,
  } = usePlayerStore();

  // ── Adjacent track items ─────────────────────────────────────────────────
  const prevTrackItem = queueIndex > 0 ? queue[queueIndex - 1] : null;
  const nextTrackItem = (() => {
    if (shuffle) return null; // next is random — indeterminate
    if (repeat && queueIndex === queue.length - 1) return queue[0] ?? null; // wrap
    return queue[queueIndex + 1] ?? null;
  })();

  // ── Progress ─────────────────────────────────────────────────────────────
  const progressPct = duration > 0 ? (position / duration) * 100 : 0;

  function handleProgressClick(e: React.MouseEvent<HTMLDivElement>) {
    if (!progressRef.current || duration <= 0) return;
    const rect = progressRef.current.getBoundingClientRect();
    const pct  = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    onSeek(pct * duration);
  }

  // ── Settings navigation — exit vinyl and show the settings page ──────────
  function handleSettings() {
    onMinimize(); // drop to mini bar so content is visible
    navigate({ to: '/settings' });
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full min-h-0 flex-col bg-base overflow-hidden relative">

      {/* ── Queue side panel ─────────────────────────────────────────────── */}
      {showQueue && (
        <div className="absolute right-0 top-0 bottom-0 w-72 bg-[#0c0c0c] border-l border-[#181818] z-20 flex flex-col shadow-2xl">
          <div className="flex items-center justify-between px-5 py-3.5 border-b border-[#181818] flex-shrink-0">
            <span className="font-mono text-[11px] text-text-muted uppercase tracking-widest">
              Queue · {queue.length} track{queue.length !== 1 ? 's' : ''}
            </span>
            <button
              onClick={() => setShowQueue(false)}
              className="text-text-muted hover:text-text-secondary transition-all active:scale-90"
              aria-label="Close queue"
            >
              <ChevronDown size={14} />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto">
            {queue.length === 0 ? (
              <p className="text-[12px] text-text-muted font-mono text-center mt-8 px-5 leading-relaxed">
                Queue empty —<br />play something from your library
              </p>
            ) : (
              <ul>
                {queue.map((track, i) => (
                  <li key={`${track.id}-${i}`}>
                    <button
                      onClick={() => playAt(i)}
                      className={`w-full text-left px-5 py-2.5 flex items-center gap-3 hover:bg-[#111] transition-colors ${
                        i === queueIndex ? 'text-accent' : ''
                      }`}
                    >
                      <span className="font-mono text-[10px] text-text-muted w-4 flex-shrink-0 text-right">
                        {i === queueIndex && isPlaying ? '▶' : i + 1}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className={`text-[12px] truncate leading-tight ${
                          i === queueIndex ? 'text-accent' : 'text-text-primary'
                        }`}>
                          {track.title}
                        </p>
                        <p className="font-mono text-[10px] text-text-muted truncate leading-tight">
                          {track.artist}
                        </p>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {/* ── Minimal top header ────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-5 pt-4 pb-1 flex-shrink-0">
        {/* Minimize — ChevronDown is a natural "push down" affordance */}
        <button
          onClick={onMinimize}
          className="flex items-center gap-1.5 text-text-muted hover:text-text-secondary transition-all active:scale-95 group"
          aria-label="Minimize player"
        >
          <ChevronDown size={19} />
          <span className="text-[10px] font-mono tracking-wide opacity-0 group-hover:opacity-70 transition-opacity">
            Minimize
          </span>
        </button>
        {/* Switch back to standard full-page view */}
        <button
          onClick={expand}
          className="text-text-muted hover:text-text-secondary transition-all active:scale-95"
          aria-label="Standard view"
          title="Standard view"
        >
          <LayoutList size={15} />
        </button>
      </div>

      {/* ── Three-cover carousel stage ────────────────────────────────────── */}
      {/* perspective on outer; transformStyle:preserve-3d on inner row       */}
      <div
        className="flex-1 flex items-center justify-center min-h-0 px-4"
        style={{ perspective: '1000px' }}
      >
        <div
          className="flex items-center justify-center gap-4"
          style={{ transformStyle: 'preserve-3d' }}
        >

          {/* ── Previous cover — angled back-left ─────────────────────── */}
          <button
            onClick={() => prevTrackItem && prevTrack()}
            disabled={!prevTrackItem}
            aria-label="Previous track"
            className="flex-shrink-0 rounded-xl overflow-hidden focus:outline-none
                       disabled:cursor-default transition-all active:scale-95"
            style={{
              width:     'clamp(80px, 12vw, 118px)',
              height:    'clamp(80px, 12vw, 118px)',
              transform: 'rotateY(28deg) translateX(-8px) scale(0.88)',
              opacity:   prevTrackItem ? 0.52 : 0.10,
              border:    '1px solid rgba(255,255,255,0.07)',
              boxShadow: '-6px 10px 30px rgba(0,0,0,0.65)',
              transition: 'opacity 0.25s ease, transform 0.15s ease',
            }}
          >
            {/* key forces remount → fixes stale artwork bug */}
            <ArtCard key={prevTrackItem?.id ?? 'empty-prev'} track={prevTrackItem} size="sm" />
          </button>

          {/* ── Current main art — LP sleeve treatment ─────────────────── */}
          <div
            className="flex-shrink-0 rounded-2xl overflow-hidden relative"
            style={{
              width:     'clamp(200px, 37vw, 360px)',
              height:    'clamp(200px, 37vw, 360px)',
              border:    '1.5px solid rgba(255,255,255,0.07)',
              boxShadow: [
                '0 32px 80px rgba(0,0,0,0.88)',
                '0 10px 24px rgba(0,0,0,0.70)',
                'inset 0 1px 0 rgba(255,255,255,0.05)',
              ].join(', '),
            }}
          >
            <ArtCard key={currentTrack?.id ?? 'empty-curr'} track={currentTrack} size="lg" />
            {/* LP jacket top-edge sheen — mimics printed sleeve highlight */}
            <div
              className="absolute inset-x-0 top-0 h-px pointer-events-none"
              style={{ background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.11) 50%, transparent)' }}
            />
          </div>

          {/* ── Next cover — angled back-right ────────────────────────── */}
          <button
            onClick={() => nextTrackItem && nextTrack()}
            disabled={!nextTrackItem}
            aria-label="Next track"
            className="flex-shrink-0 rounded-xl overflow-hidden focus:outline-none
                       disabled:cursor-default transition-all active:scale-95"
            style={{
              width:     'clamp(80px, 12vw, 118px)',
              height:    'clamp(80px, 12vw, 118px)',
              transform: 'rotateY(-28deg) translateX(8px) scale(0.88)',
              opacity:   nextTrackItem ? 0.52 : 0.10,
              border:    '1px solid rgba(255,255,255,0.07)',
              boxShadow: '6px 10px 30px rgba(0,0,0,0.65)',
              transition: 'opacity 0.25s ease, transform 0.15s ease',
            }}
          >
            <ArtCard key={nextTrackItem?.id ?? 'empty-next'} track={nextTrackItem} size="sm" />
          </button>

        </div>
      </div>

      {/* ── Track identity block ──────────────────────────────────────────── */}
      <div className="flex-shrink-0 text-center px-8 pt-4 pb-2">
        <p className="text-[21px] font-semibold text-text-primary leading-tight truncate">
          {currentTrack?.title ?? 'Nothing playing'}
        </p>
        <p className="font-mono text-[13px] text-text-secondary leading-tight mt-1 truncate">
          {currentTrack
            ? `${currentTrack.artist}${currentTrack.album ? ` · ${currentTrack.album}` : ''}`
            : '—'}
        </p>
        {/* Decorative waveform bars */}
        <div className="mt-3 flex justify-center">
          <Waveform />
        </div>
        {/* Audio fidelity badge — codec / bitrate / bit-depth / sample-rate */}
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
      </div>

      {/* ── Bottom control bar ────────────────────────────────────────────── */}
      {/* Two rows: transport buttons (above) | progress bar (below)          */}
      <div className="flex-shrink-0 border-t border-[#131313] bg-[#060606]">

        {/* Transport row */}
        <div className="flex items-center px-4 pt-3.5 pb-2 gap-2">

          {/* ── Left: Now Playing thumbnail + names ───────────────────── */}
          <div className="flex items-center gap-2.5 w-[190px] min-w-0 flex-shrink-0">
            <div
              className="w-10 h-10 rounded-lg flex-shrink-0 overflow-hidden border border-[#1e1e1e]"
              style={{ boxShadow: '0 2px 10px rgba(0,0,0,0.55)' }}
            >
              <ArtCard key={`bar-${currentTrack?.id ?? 'empty'}`} track={currentTrack} size="sm" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-[12px] font-medium text-text-primary truncate leading-tight">
                {currentTrack?.title ?? '—'}
              </p>
              <p className="font-mono text-[10px] text-text-muted truncate leading-tight mt-0.5">
                {currentTrack?.album ?? '—'}
              </p>
            </div>
          </div>

          {/* ── Center: transport buttons ──────────────────────────────── */}
          <div className="flex-1 flex items-center justify-center gap-5">
            <button
              onClick={toggleShuffle}
              aria-label={shuffle ? 'Shuffle on' : 'Shuffle off'}
              className={`transition-all active:scale-90 ${
                shuffle ? 'text-accent' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Shuffle size={17} />
            </button>

            <button
              onClick={prevTrack}
              disabled={!currentTrack}
              aria-label="Previous"
              className="text-text-secondary hover:text-text-primary transition-all active:scale-90 disabled:opacity-25"
            >
              <SkipBack size={22} />
            </button>

            {/* Play / Pause — primary tactile button */}
            <button
              onClick={onPlayPause}
              disabled={!currentTrack}
              aria-label={isPlaying ? 'Pause' : 'Play'}
              className="w-[54px] h-[54px] rounded-full bg-accent hover:bg-accent/85
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
              <SkipForward size={22} />
            </button>

            <button
              onClick={toggleRepeat}
              aria-label={repeat ? 'Repeat on' : 'Repeat off'}
              className={`transition-all active:scale-90 ${
                repeat ? 'text-accent' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Repeat size={17} />
            </button>
          </div>

          {/* ── Right: ratings + actions + queue ──────────────────────── */}
          <div className="flex items-center justify-end gap-2 w-[190px] flex-shrink-0">

            {/* Star rating — local state placeholder */}
            <StarRating rating={starRating} onChange={setStarRating} />

            <div className="w-px h-3 bg-[#222] mx-1 flex-shrink-0" />

            {/* Like / favorite */}
            <button
              onClick={() => setLiked(v => !v)}
              aria-label={liked ? 'Unlike' : 'Like'}
              className={`transition-all active:scale-90 ${
                liked ? 'text-danger' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Heart size={14} fill={liked ? 'currentColor' : 'none'} />
            </button>

            {/* Lyrics — placeholder, not yet implemented */}
            <button
              disabled
              aria-label="Lyrics — coming soon"
              title="Lyrics — coming soon"
              className="text-text-muted opacity-30 cursor-not-allowed"
            >
              <Mic2 size={14} />
            </button>

            {/* Live / radio — placeholder */}
            <button
              disabled
              aria-label="Live — coming soon"
              title="Live — coming soon"
              className="text-text-muted opacity-30 cursor-not-allowed"
            >
              <ListMusic size={14} />
            </button>

            {/* Settings — navigates to /settings, drops to mini bar */}
            <button
              onClick={handleSettings}
              aria-label="Settings"
              title="Settings"
              className="text-text-muted hover:text-text-secondary transition-all active:scale-90"
            >
              <Settings size={14} />
            </button>

            {/* Queue toggle — badge shows track count */}
            <button
              onClick={() => setShowQueue(v => !v)}
              aria-label="Toggle queue"
              title="Queue"
              className={`relative transition-all active:scale-90 ${
                showQueue ? 'text-accent' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <List size={15} />
              {queue.length > 0 && (
                <span
                  className="absolute -top-1.5 -right-1.5 w-3.5 h-3.5 rounded-full
                             bg-accent text-black text-[8px] font-bold
                             flex items-center justify-center leading-none tabular-nums"
                >
                  {queue.length > 99 ? '99' : queue.length}
                </span>
              )}
            </button>

          </div>
        </div>

        {/* Progress bar row — slightly taller than other players to match aesthetic */}
        <div className="flex items-center gap-3 px-4 pb-5">
          <span className="font-mono text-[10px] text-text-muted tabular-nums w-8 text-right flex-shrink-0">
            {formattedPosition}
          </span>

          <div
            ref={progressRef}
            onClick={handleProgressClick}
            className="flex-1 h-[5px] bg-[#1c1c1c] rounded-full relative cursor-pointer group"
          >
            {/* Filled portion */}
            <div
              className="absolute top-0 left-0 h-full bg-accent rounded-full pointer-events-none"
              style={{ width: `${progressPct}%` }}
            />
            {/* Playhead dot — revealed on hover */}
            <div
              className="absolute top-1/2 -translate-y-1/2 w-3.5 h-3.5 bg-accent
                         rounded-full opacity-0 group-hover:opacity-100
                         transition-opacity pointer-events-none"
              style={{ left: `${progressPct}%`, marginLeft: '-7px' }}
            />
          </div>

          <span className="font-mono text-[10px] text-text-muted tabular-nums w-8 flex-shrink-0">
            {formattedDuration}
          </span>
        </div>

      </div>

    </div>
  );
}
