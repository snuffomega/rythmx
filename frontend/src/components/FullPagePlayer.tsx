/**
 * FullPagePlayer — replaces main content area when player is in 'fullpage' state.
 *
 * STUB (Phase 13b): UI chrome is complete. No real playback, no real track
 * data, no real queue. All controls except play/pause and minimize are visual
 * only. Phase 14 will add: nowPlaying track, real progress, platform play APIs,
 * queue management.
 */
import {
  Play, Pause, SkipBack, SkipForward, Square,
  Volume2, Repeat, Shuffle, ListPlus, Minimize2, Disc,
} from 'lucide-react';

interface FullPagePlayerProps {
  isPlaying: boolean;
  onPlayPause: () => void;
  onMinimize: () => void;
}

const STUB_PROGRESS = 34;  // % — static until Phase 14
const STUB_VOLUME   = 72;  // % — static until Phase 14

export function FullPagePlayer({ isPlaying, onPlayPause, onMinimize }: FullPagePlayerProps) {
  return (
    <div className="flex h-full min-h-0 overflow-hidden">

      {/* ── Left panel: Now Playing ────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0 p-8 overflow-y-auto">

        {/* Header */}
        <div className="flex items-center justify-between mb-8 flex-shrink-0">
          <h2 className="text-[13px] font-mono text-text-muted uppercase tracking-widest">
            Now Playing
          </h2>
          <button
            onClick={onMinimize}
            className="text-text-muted hover:text-text-secondary transition-colors"
            aria-label="Minimize player"
            title="Back to mini player"
          >
            <Minimize2 size={16} />
          </button>
        </div>

        {/* Album art */}
        <div className="flex justify-center mb-8 flex-shrink-0">
          <div className="w-full max-w-[400px] aspect-square bg-[#1a1a1a] rounded flex items-center justify-center border border-[#222]">
            <Disc size={80} className="text-[#333]" />
          </div>
        </div>

        {/* Track info */}
        <div className="text-center mb-6 flex-shrink-0">
          <p className="text-[22px] font-semibold text-text-primary leading-tight mb-1">
            {/* TODO Phase 14: nowPlaying.title */}
            Now Playing
          </p>
          <p className="font-mono text-[15px] text-text-secondary leading-tight mb-0.5">
            {/* TODO Phase 14: nowPlaying.artist */}
            —
          </p>
          <p className="font-mono text-[13px] text-text-muted leading-tight">
            {/* TODO Phase 14: nowPlaying.album */}
          </p>
        </div>

        {/* Progress bar — static stub */}
        <div className="max-w-[480px] mx-auto w-full mb-6 flex-shrink-0">
          <div className="flex items-center gap-3">
            <span className="font-mono text-[11px] text-text-muted tabular-nums w-9 text-right">1:48</span>
            <div className="flex-1 h-1 bg-[#1a1a1a] rounded-full relative cursor-pointer group">
              <div
                className="absolute top-0 left-0 h-full bg-accent rounded-full"
                style={{ width: `${STUB_PROGRESS}%` }}
              />
              <div
                className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-accent rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
                style={{ left: `${STUB_PROGRESS}%`, marginLeft: '-6px' }}
              />
            </div>
            <span className="font-mono text-[11px] text-text-muted tabular-nums w-9">5:18</span>
          </div>
        </div>

        {/* Transport controls */}
        <div className="flex items-center justify-center gap-7 mb-6 flex-shrink-0">
          <button className="text-text-muted hover:text-text-secondary transition-colors" aria-label="Shuffle">
            <Shuffle size={18} />
          </button>
          <button className="text-text-secondary hover:text-text-primary transition-colors" aria-label="Previous">
            <SkipBack size={22} />
          </button>
          <button className="text-text-secondary hover:text-text-primary transition-colors" aria-label="Stop">
            <Square size={20} />
          </button>
          <button
            onClick={onPlayPause}
            aria-label={isPlaying ? 'Pause' : 'Play'}
            className="w-14 h-14 rounded-full bg-accent hover:bg-accent/80 flex items-center justify-center transition-colors"
          >
            {isPlaying
              ? <Pause size={22} className="text-black" />
              : <Play  size={22} className="text-black ml-0.5" />}
          </button>
          <button className="text-text-secondary hover:text-text-primary transition-colors" aria-label="Next">
            <SkipForward size={22} />
          </button>
          <button className="text-text-muted hover:text-text-secondary transition-colors" aria-label="Repeat">
            <Repeat size={18} />
          </button>
        </div>

        {/* Volume + add-to-playlist */}
        <div className="flex items-center justify-center gap-3 flex-shrink-0">
          <button className="text-text-muted hover:text-text-secondary transition-colors" aria-label="Add to playlist">
            <ListPlus size={16} />
          </button>
          <div className="w-px h-4 bg-[#222] mx-1" />
          <Volume2 size={15} className="text-text-secondary flex-shrink-0" />
          {/* Volume slider — static stub */}
          <div className="w-28 h-1 bg-[#1a1a1a] rounded-full relative cursor-pointer group">
            <div
              className="absolute top-0 left-0 h-full bg-text-secondary rounded-full"
              style={{ width: `${STUB_VOLUME}%` }}
            />
            <div
              className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 bg-text-primary rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
              style={{ left: `${STUB_VOLUME}%`, marginLeft: '-5px' }}
            />
          </div>
        </div>
      </div>

      {/* ── Right panel: Queue ─────────────────────────────────────────────── */}
      <div className="w-[340px] flex-shrink-0 border-l border-[#1a1a1a] flex flex-col min-h-0">

        {/* Queue header */}
        <div className="px-5 py-4 border-b border-[#1a1a1a] flex-shrink-0">
          <h3 className="text-[13px] font-mono text-text-muted uppercase tracking-widest">
            Queue · 0 tracks
          </h3>
        </div>

        {/* Queue list */}
        <div className="flex-1 overflow-y-auto p-5">
          <p className="text-[13px] text-text-muted font-mono text-center mt-8 leading-relaxed">
            Queue empty —{' '}
            <br />
            play something from your library
          </p>
          {/* TODO Phase 14: render queue items */}
        </div>
      </div>
    </div>
  );
}
