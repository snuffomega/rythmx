/**
 * FullPagePlayer — replaces main content area when playerState === 'fullpage'.
 *
 * Left panel: album art, track info, progress, controls, volume.
 * Right panel: queue list — click any item to jump to it.
 */
import {
  Play, Pause, SkipBack, SkipForward,
  Volume2, Repeat, Shuffle, ListPlus, Minimize2, Disc,
} from 'lucide-react';
import { useRef } from 'react';
import { usePlayerStore } from '../stores/usePlayerStore';
// isPlaying is still a prop here (owned by __root → useAudioEngine) but
// repeat / shuffle / showVinyl are pure store actions, read directly.
import { AudioQualityBadge } from './common';
import { useImage } from '../hooks/useImage';

interface FullPagePlayerProps {
  isPlaying: boolean;
  onPlayPause: () => void;
  onMinimize: () => void;
  onSeek: (seconds: number) => void;
  onVolumeChange: (vol: number) => void;
}

function TrackArt({ thumbUrl, title, artist }: { thumbUrl: string | null; title: string; artist: string }) {
  const resolved = useImage('album', title, artist);
  const src = thumbUrl ?? resolved ?? null;
  if (src) return <img src={src} alt="" className="w-full max-w-[400px] aspect-square object-cover rounded border border-[#222]" />;
  return (
    <div className="w-full max-w-[400px] aspect-square bg-[#1a1a1a] rounded flex items-center justify-center border border-[#222]">
      <Disc size={80} className="text-[#333]" />
    </div>
  );
}

export function FullPagePlayer({ isPlaying, onPlayPause, onMinimize, onSeek, onVolumeChange }: FullPagePlayerProps) {
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
    repeat,
    nextTrack,
    prevTrack,
    playAt,
    toggleShuffle,
    toggleRepeat,
    showVinyl,
  } = usePlayerStore();

  const progressRef = useRef<HTMLDivElement>(null);
  const volumeRef   = useRef<HTMLDivElement>(null);

  const progressPct = duration > 0 ? (position / duration) * 100 : 0;

  function handleProgressClick(e: React.MouseEvent<HTMLDivElement>) {
    if (!progressRef.current || duration <= 0) return;
    const rect = progressRef.current.getBoundingClientRect();
    const pct  = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    onSeek(pct * duration);
  }

  function handleVolumeClick(e: React.MouseEvent<HTMLDivElement>) {
    if (!volumeRef.current) return;
    const rect = volumeRef.current.getBoundingClientRect();
    const pct  = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    onVolumeChange(pct);
  }

  return (
    <div className="flex h-full min-h-0 overflow-hidden">

      {/* ── Left panel: Now Playing ────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0 p-8 overflow-y-auto">

        <div className="flex items-center justify-between mb-8 flex-shrink-0">
          <h2 className="text-[13px] font-mono text-text-muted uppercase tracking-widest">Now Playing</h2>
          <div className="flex items-center gap-3">
            {/* Temporary: switch to VinylPlayerScreen for testing */}
            <button
              onClick={showVinyl}
              className="text-text-muted hover:text-text-secondary transition-colors"
              aria-label="Vinyl view"
              title="Vinyl view (test)"
            >
              <Disc size={15} />
            </button>
            <button
              onClick={onMinimize}
              className="text-text-muted hover:text-text-secondary transition-colors"
              aria-label="Minimize player"
            >
              <Minimize2 size={16} />
            </button>
          </div>
        </div>

        {/* Album art */}
        <div className="flex justify-center mb-8 flex-shrink-0">
          <TrackArt thumbUrl={currentTrack?.thumb_url ?? null} title={currentTrack?.album ?? ''} artist={currentTrack?.artist ?? ''} />
        </div>

        {/* Track info */}
        <div className="text-center mb-6 flex-shrink-0">
          <p className="text-[22px] font-semibold text-text-primary leading-tight mb-1">
            {currentTrack?.title ?? 'Nothing playing'}
          </p>
          <p className="font-mono text-[15px] text-text-secondary leading-tight mb-0.5">
            {currentTrack?.artist ?? '—'}
          </p>
          <p className="font-mono text-[13px] text-text-muted leading-tight">
            {currentTrack?.album ?? ''}
          </p>
          {currentTrack && (
            <div className="flex justify-center mt-2">
              <AudioQualityBadge
                codec={currentTrack.codec}
                bitrate={currentTrack.bitrate}
                bit_depth={currentTrack.bit_depth}
                sample_rate={currentTrack.sample_rate}
              />
            </div>
          )}
        </div>

        {/* Progress bar */}
        <div className="max-w-[480px] mx-auto w-full mb-6 flex-shrink-0">
          <div className="flex items-center gap-3">
            <span className="font-mono text-[11px] text-text-muted tabular-nums w-9 text-right">
              {formattedPosition}
            </span>
            <div
              ref={progressRef}
              onClick={handleProgressClick}
              className="flex-1 h-1 bg-[#1a1a1a] rounded-full relative cursor-pointer group"
            >
              <div
                className="absolute top-0 left-0 h-full bg-accent rounded-full pointer-events-none"
                style={{ width: `${progressPct}%` }}
              />
              <div
                className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-accent rounded-full opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
                style={{ left: `${progressPct}%`, marginLeft: '-6px' }}
              />
            </div>
            <span className="font-mono text-[11px] text-text-muted tabular-nums w-9">
              {formattedDuration}
            </span>
          </div>
        </div>

        {/* Transport controls */}
        <div className="flex items-center justify-center gap-7 mb-6 flex-shrink-0">
          <button
            onClick={toggleShuffle}
            className={`transition-colors ${shuffle ? 'text-accent' : 'text-text-muted hover:text-text-secondary'}`}
            aria-label="Shuffle"
          >
            <Shuffle size={18} />
          </button>
          <button
            onClick={prevTrack}
            className="text-text-secondary hover:text-text-primary transition-colors"
            aria-label="Previous"
            disabled={!currentTrack}
          >
            <SkipBack size={22} />
          </button>
          <button
            onClick={onPlayPause}
            aria-label={isPlaying ? 'Pause' : 'Play'}
            disabled={!currentTrack}
            className="w-14 h-14 rounded-full bg-accent hover:bg-accent/80 flex items-center justify-center transition-colors disabled:opacity-40"
          >
            {isPlaying
              ? <Pause size={22} className="text-black" />
              : <Play  size={22} className="text-black ml-0.5" />}
          </button>
          <button
            onClick={nextTrack}
            className="text-text-secondary hover:text-text-primary transition-colors"
            aria-label="Next"
            disabled={!currentTrack}
          >
            <SkipForward size={22} />
          </button>
          <button
            onClick={toggleRepeat}
            className={`transition-colors ${repeat ? 'text-accent' : 'text-text-muted hover:text-text-secondary'}`}
            aria-label={repeat ? 'Repeat on' : 'Repeat off'}
          >
            <Repeat size={18} />
          </button>
        </div>

        {/* Volume */}
        <div className="flex items-center justify-center gap-3 flex-shrink-0">
          <button className="text-text-muted hover:text-text-secondary transition-colors" aria-label="Add to playlist">
            <ListPlus size={16} />
          </button>
          <div className="w-px h-4 bg-[#222] mx-1" />
          <Volume2 size={15} className="text-text-secondary flex-shrink-0" />
          <div
            ref={volumeRef}
            onClick={handleVolumeClick}
            className="w-28 h-1 bg-[#1a1a1a] rounded-full relative cursor-pointer group"
          >
            <div
              className="absolute top-0 left-0 h-full bg-text-secondary rounded-full pointer-events-none"
              style={{ width: `${volume * 100}%` }}
            />
            <div
              className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 bg-text-primary rounded-full opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
              style={{ left: `${volume * 100}%`, marginLeft: '-5px' }}
            />
          </div>
        </div>
      </div>

      {/* ── Right panel: Queue ─────────────────────────────────────────────── */}
      <div className="w-[340px] flex-shrink-0 border-l border-[#1a1a1a] flex flex-col min-h-0">
        <div className="px-5 py-4 border-b border-[#1a1a1a] flex-shrink-0 flex items-center justify-between">
          <h3 className="text-[13px] font-mono text-text-muted uppercase tracking-widest">
            Queue · {queue.length} track{queue.length !== 1 ? 's' : ''}
          </h3>
          {queue.length > 0 && (
            <button
              onClick={usePlayerStore.getState().clearQueue}
              className="text-[11px] font-mono text-text-muted hover:text-text-secondary transition-colors"
            >
              Clear
            </button>
          )}
        </div>
        <div className="flex-1 overflow-y-auto">
          {queue.length === 0 ? (
            <p className="text-[13px] text-text-muted font-mono text-center mt-8 leading-relaxed px-5">
              Queue empty —<br />play something from your library
            </p>
          ) : (
            <ul>
              {queue.map((track, i) => (
                <li key={`${track.id}-${i}`}>
                  <button
                    onClick={() => playAt(i)}
                    className={`w-full text-left px-5 py-3 flex items-center gap-3 hover:bg-[#111] transition-colors ${
                      i === queueIndex ? 'text-accent' : 'text-text-secondary'
                    }`}
                  >
                    <span className="font-mono text-[11px] text-text-muted w-5 flex-shrink-0 text-right">
                      {i === queueIndex && isPlaying ? '▶' : i + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className={`text-[13px] truncate leading-tight ${i === queueIndex ? 'text-accent' : 'text-text-primary'}`}>
                        {track.title}
                      </p>
                      <p className="font-mono text-[11px] text-text-muted truncate leading-tight">
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
    </div>
  );
}
