/**
 * VinylPlayerScreen — immersive vinyl jacket aesthetic full-screen player.
 *
 * Layout (top → bottom):
 *   1. Minimal header: ChevronDown (minimize) + LayoutList (standard view)
 *   2. Three-cover carousel (flex-1) — perspective 3D, side covers angled back
 *   3. Track identity block (title · artist · album · waveform · quality badge)
 *      — sits cleanly below the artwork with deliberate breathing room
 *   4. Bottom control bar (border-t, dark bg):
 *        Left  : [thumb] [title — large, one line] [artist / album — stacked right]
 *        Center: Shuffle · Prev · Play · Next · Repeat · Volume(toggle)
 *        Right : ★★★★☆ · ♥ · Mic2 · ListMusic · Settings · Queue(toggle)
 *      Floating above bar:
 *        Queue panel  — anchored bottom-right, scrollable track list
 *        Volume panel — anchored above volume button, horizontal slider
 *   5. Progress row (5px tall bar, click-to-seek, time timestamps)
 *
 * Artwork update bug fix:
 *   useImage lives inside ArtCard; parent passes key={track?.id} so React
 *   fully remounts the card on track change, resetting useState in useImage.
 *
 * Architecture:
 *   - Pure store consumer — no audio logic. onPlayPause / onSeek / onVolumeChange
 *     are owned by __root.tsx → useAudioEngine.
 *   - starRating / liked / showQueue / showVolume are local UI state.
 */
import { useRef, useState } from 'react';
import {
  Play, Pause, SkipBack, SkipForward,
  Shuffle, Repeat, LayoutList, Settings,
  Disc, Star, Heart, ListMusic, Mic2,
  ChevronDown, List, Volume2,
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
  onVolumeChange: (vol: number) => void;
}

// ── Decorative waveform ──────────────────────────────────────────────────────
// Static bar heights — cosmetic; evokes waveform without Web Audio API.
const WAVE_HEIGHTS = [4, 7, 11, 15, 9, 18, 13, 7, 20, 12, 16, 9, 5, 14, 10, 7, 17, 12, 8, 18, 13, 9, 4, 14, 10, 7, 18, 12, 6, 15];

function Waveform() {
  return (
    <div className="flex items-center justify-center gap-[2.5px]" style={{ height: '22px' }}>
      {WAVE_HEIGHTS.map((h, i) => (
        <div
          key={i}
          className="rounded-full flex-shrink-0"
          style={{ width: '2.5px', height: `${h}px`, background: 'rgba(212,245,60,0.22)' }}
        />
      ))}
    </div>
  );
}

// ── Album art card ───────────────────────────────────────────────────────────
// useImage is called here (not in parent). Parent passes key={track?.id} so
// React remounts this on track change → fresh image resolution every skip.
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
      <Disc size={size === 'lg' ? 56 : 20} className="text-[#252525]" />
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
          className="p-1 transition-all active:scale-90"
        >
          <Star
            size={14}
            className={`transition-colors ${n <= (hover || rating) ? 'text-accent' : 'text-[#2e2e2e]'}`}
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
  onVolumeChange,
}: VinylPlayerScreenProps) {
  const navigate    = useNavigate();
  const progressRef = useRef<HTMLDivElement>(null);

  // Local UI state
  const [starRating, setStarRating] = useState(0);
  const [liked,      setLiked]      = useState(false);
  const [showQueue,  setShowQueue]  = useState(false);
  const [showVolume, setShowVolume] = useState(false);

  const {
    currentTrack,
    queue,
    queueIndex,
    position,
    duration,
    volume,
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
    setVolume: storeSetVolume,
  } = usePlayerStore();

  // ── Adjacent track items ─────────────────────────────────────────────────
  const prevTrackItem = queueIndex > 0 ? queue[queueIndex - 1] : null;
  const nextTrackItem = (() => {
    if (shuffle) return null;
    if (repeat && queueIndex === queue.length - 1) return queue[0] ?? null;
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

  function handleVolumeChange(val: number) {
    const clamped = Math.max(0, Math.min(1, val));
    storeSetVolume(clamped);  // keep store in sync for display
    onVolumeChange(clamped);  // drive the actual audio element
  }

  function handleSettings() {
    onMinimize();
    navigate({ to: '/settings' });
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full min-h-0 flex-col bg-base overflow-hidden">

      {/* ── Minimal top header ────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-5 pt-4 pb-1 flex-shrink-0">
        <button
          onClick={onMinimize}
          className="flex items-center gap-1.5 text-text-muted hover:text-text-secondary
                     transition-all active:scale-95 group"
          aria-label="Minimize player"
        >
          <ChevronDown size={20} />
          <span className="text-[10px] font-mono tracking-wide opacity-0 group-hover:opacity-60 transition-opacity">
            Minimize
          </span>
        </button>
        <button
          onClick={expand}
          className="text-text-muted hover:text-text-secondary transition-all active:scale-95"
          aria-label="Standard view"
          title="Standard view"
        >
          <LayoutList size={16} />
        </button>
      </div>

      {/* ── Three-cover carousel stage ────────────────────────────────────── */}
      <div
        className="flex-1 flex items-center justify-center min-h-0 px-4"
        style={{ perspective: '1000px' }}
      >
        <div className="flex items-center justify-center gap-4" style={{ transformStyle: 'preserve-3d' }}>

          {/* Previous cover — angled back-left */}
          <button
            onClick={() => prevTrackItem && prevTrack()}
            disabled={!prevTrackItem}
            aria-label="Previous track"
            className="flex-shrink-0 rounded-xl overflow-hidden focus:outline-none
                       disabled:cursor-default transition-all active:scale-95"
            style={{
              width:      'clamp(80px, 12vw, 118px)',
              height:     'clamp(80px, 12vw, 118px)',
              transform:  'rotateY(28deg) translateX(-8px) scale(0.88)',
              opacity:    prevTrackItem ? 0.52 : 0.10,
              border:     '1px solid rgba(255,255,255,0.07)',
              boxShadow:  '-6px 10px 30px rgba(0,0,0,0.65)',
              transition: 'opacity 0.25s ease, transform 0.15s ease',
            }}
          >
            <ArtCard key={prevTrackItem?.id ?? 'empty-prev'} track={prevTrackItem} size="sm" />
          </button>

          {/* Current main art — LP sleeve */}
          <div
            className="flex-shrink-0 rounded-2xl overflow-hidden relative"
            style={{
              width:     'clamp(200px, 37vw, 360px)',
              height:    'clamp(200px, 37vw, 360px)',
              border:    '1.5px solid rgba(255,255,255,0.07)',
              boxShadow: '0 32px 80px rgba(0,0,0,0.88), 0 10px 24px rgba(0,0,0,0.70), inset 0 1px 0 rgba(255,255,255,0.05)',
            }}
          >
            <ArtCard key={currentTrack?.id ?? 'empty-curr'} track={currentTrack} size="lg" />
            {/* LP jacket top-edge sheen */}
            <div
              className="absolute inset-x-0 top-0 h-px pointer-events-none"
              style={{ background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.11) 50%, transparent)' }}
            />
          </div>

          {/* Next cover — angled back-right */}
          <button
            onClick={() => nextTrackItem && nextTrack()}
            disabled={!nextTrackItem}
            aria-label="Next track"
            className="flex-shrink-0 rounded-xl overflow-hidden focus:outline-none
                       disabled:cursor-default transition-all active:scale-95"
            style={{
              width:      'clamp(80px, 12vw, 118px)',
              height:     'clamp(80px, 12vw, 118px)',
              transform:  'rotateY(-28deg) translateX(8px) scale(0.88)',
              opacity:    nextTrackItem ? 0.52 : 0.10,
              border:     '1px solid rgba(255,255,255,0.07)',
              boxShadow:  '6px 10px 30px rgba(0,0,0,0.65)',
              transition: 'opacity 0.25s ease, transform 0.15s ease',
            }}
          >
            <ArtCard key={nextTrackItem?.id ?? 'empty-next'} track={nextTrackItem} size="sm" />
          </button>

        </div>
      </div>

      {/* ── Track identity — deliberate gap below art ─────────────────────── */}
      <div className="flex-shrink-0 text-center px-8 pt-6 pb-3">
        <p className="text-[22px] font-semibold text-text-primary leading-tight truncate">
          {currentTrack?.title ?? 'Nothing playing'}
        </p>
        <p className="font-mono text-[13px] text-text-secondary leading-tight mt-1.5 truncate">
          {currentTrack
            ? `${currentTrack.artist}${currentTrack.album ? ` · ${currentTrack.album}` : ''}`
            : '—'}
        </p>
        <div className="mt-3 flex justify-center">
          <Waveform />
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
      </div>

      {/* ── Bottom control bar ────────────────────────────────────────────── */}
      {/* relative so queue/volume popups can anchor to bottom-full           */}
      <div className="flex-shrink-0 border-t border-[#131313] bg-[#060606] relative">

        {/* ── Queue popup — floats above bar, anchored bottom-right ─────── */}
        {showQueue && (
          <div
            className="absolute right-4 bottom-full mb-2 w-[300px] max-h-[50vh]
                       bg-[#0d0d0d] border border-[#1e1e1e] rounded-xl
                       flex flex-col shadow-2xl overflow-hidden z-30"
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-[#1a1a1a] flex-shrink-0">
              <span className="font-mono text-[11px] text-text-muted uppercase tracking-widest">
                Queue · {queue.length} track{queue.length !== 1 ? 's' : ''}
              </span>
              {queue.length > 0 && (
                <button
                  onClick={() => usePlayerStore.getState().clearQueue()}
                  className="text-[10px] font-mono text-text-muted hover:text-text-secondary transition-colors"
                >
                  Clear
                </button>
              )}
            </div>
            <div className="flex-1 overflow-y-auto">
              {queue.length === 0 ? (
                <p className="text-[12px] text-text-muted font-mono text-center py-8 px-5 leading-relaxed">
                  Queue empty —<br />play something from your library
                </p>
              ) : (
                <ul>
                  {queue.map((track, i) => (
                    <li key={`${track.id}-${i}`}>
                      <button
                        onClick={() => { playAt(i); setShowQueue(false); }}
                        className={`w-full text-left px-4 py-2.5 flex items-center gap-2.5
                                    hover:bg-[#111] transition-colors`}
                      >
                        <span className="font-mono text-[10px] text-text-muted w-5 flex-shrink-0 text-right">
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

        {/* ── Volume popup — floats above bar, anchored to volume button ─── */}
        {showVolume && (
          <div
            className="absolute bottom-full mb-2 bg-[#0d0d0d] border border-[#1e1e1e]
                       rounded-xl px-5 py-4 shadow-2xl z-30 flex flex-col gap-2.5"
            style={{
              // Center above the volume button — roughly 2/3 from left (center+right section)
              left: '50%',
              transform: 'translateX(-50%)',
              width: '220px',
            }}
          >
            <div className="flex items-center justify-between">
              <span className="font-mono text-[10px] text-text-muted uppercase tracking-widest">Volume</span>
              <span className="font-mono text-[11px] text-text-secondary tabular-nums">
                {Math.round(volume * 100)}%
              </span>
            </div>
            {/* Horizontal range slider — large enough to be usable */}
            <input
              type="range"
              min={0}
              max={100}
              value={Math.round(volume * 100)}
              onChange={e => handleVolumeChange(Number(e.target.value) / 100)}
              aria-label="Volume"
              style={{
                width: '100%',
                height: '6px',
                accentColor: '#D4F53C',
                cursor: 'pointer',
              }}
            />
          </div>
        )}

        {/* ── Transport row ─────────────────────────────────────────────── */}
        <div className="flex items-center px-5 pt-4 pb-2 gap-3">

          {/* Left: thumbnail + title (large) + artist/album (stacked right) */}
          <div className="flex items-center gap-3 flex-shrink-0" style={{ width: '230px' }}>
            {/* Thumbnail */}
            <div
              className="w-10 h-10 rounded-lg flex-shrink-0 overflow-hidden border border-[#1e1e1e]"
              style={{ boxShadow: '0 2px 8px rgba(0,0,0,0.5)' }}
            >
              <ArtCard key={`bar-${currentTrack?.id ?? 'empty'}`} track={currentTrack} size="sm" />
            </div>
            {/* Song title — large, one line, grows */}
            <p className="text-[14px] font-semibold text-text-primary truncate flex-1 leading-tight">
              {currentTrack?.title ?? '—'}
            </p>
            {/* Artist + album stacked to the right of the title */}
            <div className="flex-shrink-0 text-right max-w-[72px]">
              <p className="text-[10px] font-mono text-text-secondary truncate leading-tight">
                {currentTrack?.artist ?? ''}
              </p>
              <p className="text-[10px] font-mono text-text-muted truncate leading-tight mt-0.5">
                {currentTrack?.album ?? ''}
              </p>
            </div>
          </div>

          {/* Center: transport + volume */}
          <div className="flex-1 flex items-center justify-center gap-5">
            <button
              onClick={toggleShuffle}
              aria-label={shuffle ? 'Shuffle on' : 'Shuffle off'}
              className={`p-1.5 rounded-lg transition-all active:scale-90 ${
                shuffle ? 'text-accent' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Shuffle size={18} />
            </button>

            <button
              onClick={prevTrack}
              disabled={!currentTrack}
              aria-label="Previous"
              className="p-1.5 rounded-lg text-text-secondary hover:text-text-primary
                         transition-all active:scale-90 disabled:opacity-25"
            >
              <SkipBack size={23} />
            </button>

            {/* Play / Pause */}
            <button
              onClick={onPlayPause}
              disabled={!currentTrack}
              aria-label={isPlaying ? 'Pause' : 'Play'}
              className="w-[56px] h-[56px] rounded-full bg-accent hover:bg-accent/85
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
              className="p-1.5 rounded-lg text-text-secondary hover:text-text-primary
                         transition-all active:scale-90 disabled:opacity-25"
            >
              <SkipForward size={23} />
            </button>

            <button
              onClick={toggleRepeat}
              aria-label={repeat ? 'Repeat on' : 'Repeat off'}
              className={`p-1.5 rounded-lg transition-all active:scale-90 ${
                repeat ? 'text-accent' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Repeat size={18} />
            </button>

            {/* Volume — toggles floating slider above bar */}
            <button
              onClick={() => { setShowVolume(v => !v); setShowQueue(false); }}
              aria-label="Volume"
              title="Volume"
              className={`p-1.5 rounded-lg transition-all active:scale-90 ${
                showVolume ? 'text-accent' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Volume2 size={18} />
            </button>
          </div>

          {/* Right: ratings + actions — larger icons, padded, not at edge */}
          <div className="flex items-center justify-end gap-1 flex-shrink-0 pr-1" style={{ width: '230px' }}>

            <StarRating rating={starRating} onChange={setStarRating} />

            <div className="w-px h-4 bg-[#222] mx-2 flex-shrink-0" />

            {/* Like */}
            <button
              onClick={() => setLiked(v => !v)}
              aria-label={liked ? 'Unlike' : 'Like'}
              className={`p-2 rounded-lg transition-all active:scale-90 ${
                liked ? 'text-danger' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Heart size={17} fill={liked ? 'currentColor' : 'none'} />
            </button>

            {/* Lyrics — coming soon */}
            <button
              disabled
              aria-label="Lyrics — coming soon"
              title="Lyrics — coming soon"
              className="p-2 rounded-lg text-text-muted opacity-30 cursor-not-allowed"
            >
              <Mic2 size={17} />
            </button>

            {/* Live — coming soon */}
            <button
              disabled
              aria-label="Live — coming soon"
              title="Live — coming soon"
              className="p-2 rounded-lg text-text-muted opacity-30 cursor-not-allowed"
            >
              <ListMusic size={17} />
            </button>

            {/* Settings */}
            <button
              onClick={handleSettings}
              aria-label="Settings"
              title="Settings"
              className="p-2 rounded-lg text-text-muted hover:text-text-secondary
                         transition-all active:scale-90"
            >
              <Settings size={17} />
            </button>

            {/* Queue toggle — badge + popup anchored above bar */}
            <button
              onClick={() => { setShowQueue(v => !v); setShowVolume(false); }}
              aria-label="Toggle queue"
              title="Queue"
              className={`relative p-2 rounded-lg transition-all active:scale-90 ${
                showQueue ? 'text-accent' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <List size={18} />
              {queue.length > 0 && (
                <span
                  className="absolute top-1 right-1 w-3.5 h-3.5 rounded-full
                             bg-accent text-black text-[8px] font-bold
                             flex items-center justify-center leading-none tabular-nums"
                >
                  {queue.length > 99 ? '99' : queue.length}
                </span>
              )}
            </button>

          </div>
        </div>

        {/* ── Progress bar row ──────────────────────────────────────────── */}
        <div className="flex items-center gap-3 px-5 pb-5">
          <span className="font-mono text-[10px] text-text-muted tabular-nums w-9 text-right flex-shrink-0">
            {formattedPosition}
          </span>

          <div
            ref={progressRef}
            onClick={handleProgressClick}
            className="flex-1 h-[5px] bg-[#1c1c1c] rounded-full relative cursor-pointer group"
          >
            <div
              className="absolute top-0 left-0 h-full bg-accent rounded-full pointer-events-none"
              style={{ width: `${progressPct}%` }}
            />
            <div
              className="absolute top-1/2 -translate-y-1/2 w-3.5 h-3.5 bg-accent rounded-full
                         opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
              style={{ left: `${progressPct}%`, marginLeft: '-7px' }}
            />
          </div>

          <span className="font-mono text-[10px] text-text-muted tabular-nums w-9 flex-shrink-0">
            {formattedDuration}
          </span>
        </div>

      </div>

    </div>
  );
}
