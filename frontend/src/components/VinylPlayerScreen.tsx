/**
 * VinylPlayerScreen — immersive vinyl jacket aesthetic full-screen player.
 *
 * Vertical layout (top → bottom):
 *   1. Minimal header: ChevronDown (left) · LayoutList (right)
 *   2. Three-cover carousel (flex-1, perspective 3D)
 *        — asymmetric: left prev is smaller, right next is larger (depth cue)
 *   3. Track identity (below art, with breathing room):
 *        Song Title (large)
 *        Artist · Album  ·  ★★★★☆  ·  quality badge
 *        Decorative waveform
 *   4. Scrobble bar (time · bar · time)
 *   5. Button row — 3-column grid:
 *        Left  : misc (shuffle, repeat, like, settings)
 *        Center: Prev · Play/Pause · Next
 *        Right : Volume (popup) · Queue (popup, large icon)
 *
 * Queue / Volume popups float above the button row and are mutually exclusive.
 *
 * Artwork update fix: useImage lives inside ArtCard; parent passes key={id}
 * so React remounts on track change → fresh resolution every skip.
 *
 * Architecture: pure store consumer. onPlayPause / onSeek / onVolumeChange
 * are owned by __root.tsx → useAudioEngine.
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
const WAVE_HEIGHTS = [4, 7, 11, 15, 9, 18, 13, 7, 20, 12, 16, 9, 5, 14, 10, 7, 17, 12, 8, 18, 13, 9, 4, 14, 10, 7, 18, 12, 6, 15];

function Waveform() {
  return (
    <div className="flex items-center justify-center gap-[2.5px]" style={{ height: '18px' }}>
      {WAVE_HEIGHTS.map((h, i) => (
        <div
          key={i}
          className="rounded-full flex-shrink-0"
          style={{ width: '2px', height: `${h}px`, background: 'rgba(212,245,60,0.20)' }}
        />
      ))}
    </div>
  );
}

// ── Album art card ───────────────────────────────────────────────────────────
// useImage lives here; parent passes key={track?.id} to force remount on skip.
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
      <Disc size={size === 'lg' ? 52 : 20} className="text-[#252525]" />
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
            size={13}
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

  // ── Adjacent tracks ──────────────────────────────────────────────────────
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
    onSeek(Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)) * duration);
  }

  function handleVolumeSlider(val: number) {
    const v = Math.max(0, Math.min(1, val));
    storeSetVolume(v);
    onVolumeChange(v);
  }

  function handleSettings() {
    onMinimize();
    navigate({ to: '/settings' });
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex h-full min-h-0 flex-col bg-base overflow-hidden">

      {/* ── Minimal top header ────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-5 pt-4 pb-0 flex-shrink-0">
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

      {/* ── Three-cover carousel ──────────────────────────────────────────── */}
      {/*   Left prev is smaller than right next — asymmetric depth cue.     */}
      {/*   Base side size: ~100px. Left = ×1.0, Right = ×1.30              */}
      <div
        className="flex-1 flex items-center justify-center min-h-0 px-4"
        style={{ perspective: '1000px' }}
      >
        <div className="flex items-center justify-center gap-5" style={{ transformStyle: 'preserve-3d' }}>

          {/* Previous — smaller, angled left */}
          <button
            onClick={() => prevTrackItem && prevTrack()}
            disabled={!prevTrackItem}
            aria-label="Previous track"
            className="flex-shrink-0 rounded-xl overflow-hidden focus:outline-none
                       disabled:cursor-default transition-all active:scale-95"
            style={{
              width:      'clamp(72px, 10vw, 100px)',
              height:     'clamp(72px, 10vw, 100px)',
              transform:  'rotateY(30deg) translateX(-10px) scale(0.86)',
              opacity:    prevTrackItem ? 0.50 : 0.08,
              border:     '1px solid rgba(255,255,255,0.07)',
              boxShadow:  '-8px 12px 32px rgba(0,0,0,0.70)',
              transition: 'opacity 0.25s, transform 0.15s',
            }}
          >
            <ArtCard key={prevTrackItem?.id ?? 'empty-prev'} track={prevTrackItem} size="sm" />
          </button>

          {/* Current — LP sleeve, centered, dominant */}
          <div
            className="flex-shrink-0 rounded-2xl overflow-hidden relative"
            style={{
              width:     'clamp(200px, 38vw, 370px)',
              height:    'clamp(200px, 38vw, 370px)',
              border:    '1.5px solid rgba(255,255,255,0.07)',
              boxShadow: '0 36px 90px rgba(0,0,0,0.90), 0 12px 28px rgba(0,0,0,0.72), inset 0 1px 0 rgba(255,255,255,0.05)',
            }}
          >
            <ArtCard key={currentTrack?.id ?? 'empty-curr'} track={currentTrack} size="lg" />
            {/* Printed-sleeve top sheen */}
            <div
              className="absolute inset-x-0 top-0 h-px pointer-events-none"
              style={{ background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.12) 50%, transparent)' }}
            />
          </div>

          {/* Next — 30% larger than prev, angled right — feels like it's coming forward */}
          <button
            onClick={() => nextTrackItem && nextTrack()}
            disabled={!nextTrackItem}
            aria-label="Next track"
            className="flex-shrink-0 rounded-xl overflow-hidden focus:outline-none
                       disabled:cursor-default transition-all active:scale-95"
            style={{
              width:      'clamp(94px, 13vw, 130px)',
              height:     'clamp(94px, 13vw, 130px)',
              transform:  'rotateY(-30deg) translateX(10px) scale(0.92)',
              opacity:    nextTrackItem ? 0.58 : 0.08,
              border:     '1px solid rgba(255,255,255,0.08)',
              boxShadow:  '8px 12px 32px rgba(0,0,0,0.70)',
              transition: 'opacity 0.25s, transform 0.15s',
            }}
          >
            <ArtCard key={nextTrackItem?.id ?? 'empty-next'} track={nextTrackItem} size="sm" />
          </button>

        </div>
      </div>

      {/* ── Track identity — breathing room below art ─────────────────────── */}
      <div className="flex-shrink-0 text-center px-8 pt-5 pb-0">
        {/* Title */}
        <p className="text-[22px] font-semibold text-text-primary leading-tight truncate">
          {currentTrack?.title ?? 'Nothing playing'}
        </p>
        {/* Artist · Album  +  stars  +  quality badge — all on one line */}
        <div className="flex items-center justify-center gap-3 mt-1.5 flex-wrap">
          <p className="font-mono text-[13px] text-text-secondary leading-tight truncate">
            {currentTrack
              ? `${currentTrack.artist}${currentTrack.album ? ` · ${currentTrack.album}` : ''}`
              : '—'}
          </p>
          <StarRating rating={starRating} onChange={setStarRating} />
          {currentTrack && (
            <AudioQualityBadge
              codec={currentTrack.codec}
              bitrate={currentTrack.bitrate}
              bit_depth={currentTrack.bit_depth}
              sample_rate={currentTrack.sample_rate}
            />
          )}
        </div>
        {/* Decorative waveform */}
        <div className="mt-2.5 flex justify-center">
          <Waveform />
        </div>
      </div>

      {/* ── Scrobble bar ─────────────────────────────────────────────────── */}
      <div className="flex-shrink-0 px-8 pt-4 pb-0">
        <div className="flex items-center gap-3">
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

      {/* ── Button row — relative so popups can anchor bottom-full ──────── */}
      <div className="flex-shrink-0 relative pb-6 pt-3 px-6">

        {/* ── Queue popup — floats above the Queue button ──────────────── */}
        {showQueue && (
          <div
            className="absolute right-6 bottom-full mb-2 w-[300px] max-h-[48vh]
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
                        className="w-full text-left px-4 py-2.5 flex items-center gap-2.5 hover:bg-[#111] transition-colors"
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

        {/* ── Volume popup — floats above volume button ────────────────── */}
        {showVolume && (
          <div
            className="absolute bottom-full mb-2 bg-[#0d0d0d] border border-[#1e1e1e]
                       rounded-xl px-5 py-4 shadow-2xl z-30 flex flex-col gap-3"
            style={{ right: '56px', width: '200px' }}
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

        {/* ── 3-column button grid ─────────────────────────────────────── */}
        <div className="grid grid-cols-3 items-center">

          {/* Left: misc controls */}
          <div className="flex items-center gap-1">
            <button
              onClick={toggleShuffle}
              aria-label={shuffle ? 'Shuffle on' : 'Shuffle off'}
              className={`p-2 rounded-lg transition-all active:scale-90 ${
                shuffle ? 'text-accent' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Shuffle size={18} />
            </button>
            <button
              onClick={toggleRepeat}
              aria-label={repeat ? 'Repeat on' : 'Repeat off'}
              className={`p-2 rounded-lg transition-all active:scale-90 ${
                repeat ? 'text-accent' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Repeat size={18} />
            </button>
            <button
              onClick={() => setLiked(v => !v)}
              aria-label={liked ? 'Unlike' : 'Like'}
              className={`p-2 rounded-lg transition-all active:scale-90 ${
                liked ? 'text-danger' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Heart size={18} fill={liked ? 'currentColor' : 'none'} />
            </button>
            <button
              disabled
              aria-label="Lyrics — coming soon"
              title="Lyrics — coming soon"
              className="p-2 rounded-lg text-text-muted opacity-25 cursor-not-allowed"
            >
              <Mic2 size={18} />
            </button>
            <button
              disabled
              aria-label="Live — coming soon"
              title="Live — coming soon"
              className="p-2 rounded-lg text-text-muted opacity-25 cursor-not-allowed"
            >
              <ListMusic size={18} />
            </button>
            <button
              onClick={handleSettings}
              aria-label="Settings"
              title="Settings"
              className="p-2 rounded-lg text-text-muted hover:text-text-secondary transition-all active:scale-90"
            >
              <Settings size={18} />
            </button>
          </div>

          {/* Center: primary transport */}
          <div className="flex items-center justify-center gap-5">
            <button
              onClick={prevTrack}
              disabled={!currentTrack}
              aria-label="Previous"
              className="p-1.5 text-text-secondary hover:text-text-primary
                         transition-all active:scale-90 disabled:opacity-25"
            >
              <SkipBack size={24} />
            </button>

            <button
              onClick={onPlayPause}
              disabled={!currentTrack}
              aria-label={isPlaying ? 'Pause' : 'Play'}
              className="w-[60px] h-[60px] rounded-full bg-accent hover:bg-accent/85
                         flex items-center justify-center flex-shrink-0
                         transition-all active:scale-90 disabled:opacity-25"
            >
              {isPlaying
                ? <Pause size={24} className="text-black" />
                : <Play  size={24} className="text-black ml-0.5" />}
            </button>

            <button
              onClick={nextTrack}
              disabled={!currentTrack}
              aria-label="Next"
              className="p-1.5 text-text-secondary hover:text-text-primary
                         transition-all active:scale-90 disabled:opacity-25"
            >
              <SkipForward size={24} />
            </button>
          </div>

          {/* Right: volume + queue */}
          <div className="flex items-center justify-end gap-2">
            {/* Volume */}
            <button
              onClick={() => { setShowVolume(v => !v); setShowQueue(false); }}
              aria-label="Volume"
              title="Volume"
              className={`p-2.5 rounded-lg transition-all active:scale-90 ${
                showVolume ? 'text-accent' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Volume2 size={22} />
            </button>

            {/* Queue — larger icon, badge */}
            <button
              onClick={() => { setShowQueue(v => !v); setShowVolume(false); }}
              aria-label="Toggle queue"
              title="Queue"
              className={`relative p-2.5 rounded-lg transition-all active:scale-90 ${
                showQueue ? 'text-accent' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <List size={26} />
              {queue.length > 0 && (
                <span
                  className="absolute top-1.5 right-1.5 w-3.5 h-3.5 rounded-full
                             bg-accent text-black text-[8px] font-bold
                             flex items-center justify-center leading-none tabular-nums"
                >
                  {queue.length > 99 ? '99' : queue.length}
                </span>
              )}
            </button>
          </div>

        </div>
      </div>

    </div>
  );
}
