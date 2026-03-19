/**
 * PlayerBar — 80px mini player bar, fixed at the bottom of the app layout.
 *
 * STUB (Phase 13b): UI chrome is complete. No real playback, no real track
 * data, no real queue. Progress, volume, and skip controls are visual only.
 * Phase 14 will add: nowPlaying track, real progress, platform play APIs.
 */
import {
  Play, Pause, SkipBack, SkipForward, Square,
  Volume2, Repeat, Shuffle, ListPlus, Maximize2, ChevronDown, Disc,
} from 'lucide-react';

interface PlayerBarProps {
  isPlaying: boolean;
  onPlayPause: () => void;
  onExpand: () => void;
  onMinimize: () => void;
}

const STUB_PROGRESS = 34;   // % — static until Phase 14
const STUB_VOLUME   = 72;   // % — static until Phase 14

export function PlayerBar({ isPlaying, onPlayPause, onExpand, onMinimize }: PlayerBarProps) {
  return (
    <div className="fixed bottom-0 left-16 right-0 z-30 h-[80px] bg-[#0a0a0a] border-t border-[#1a1a1a] flex items-center px-5 gap-5">

      {/* ── Left: track info ─────────────────────────────────────────────── */}
      <div className="flex items-center gap-3.5 w-[280px] min-w-0">
        <div className="w-12 h-12 bg-[#1a1a1a] rounded-sm flex-shrink-0 flex items-center justify-center border border-[#222]">
          <Disc size={20} className="text-text-muted" />
        </div>
        <div className="min-w-0">
          <p className="text-[15px] text-text-primary truncate leading-tight">
            {/* TODO Phase 14: nowPlaying.title */}
            Now Playing
          </p>
          <p className="font-mono text-[13px] text-text-secondary truncate leading-tight mt-0.5">
            {/* TODO Phase 14: nowPlaying.artist */}
            —
          </p>
          <p className="font-mono text-[11px] text-text-muted truncate leading-tight mt-0.5">
            {/* TODO Phase 14: nowPlaying.album */}
          </p>
        </div>
      </div>

      {/* ── Center: controls + progress ──────────────────────────────────── */}
      <div className="flex-1 flex flex-col items-center justify-center gap-1.5 max-w-[640px] mx-auto">
        <div className="flex items-center gap-5">
          <button className="text-text-muted hover:text-text-secondary transition-colors" aria-label="Shuffle">
            <Shuffle size={15} />
          </button>
          <button className="text-text-secondary hover:text-text-primary transition-colors" aria-label="Previous">
            <SkipBack size={18} />
          </button>
          <button className="text-text-secondary hover:text-text-primary transition-colors" aria-label="Stop">
            <Square size={18} />
          </button>
          <button
            onClick={onPlayPause}
            aria-label={isPlaying ? 'Pause' : 'Play'}
            className="w-9 h-9 rounded-full bg-accent hover:bg-accent/80 flex items-center justify-center transition-colors"
          >
            {isPlaying
              ? <Pause size={16} className="text-black" />
              : <Play  size={16} className="text-black ml-0.5" />}
          </button>
          <button className="text-text-secondary hover:text-text-primary transition-colors" aria-label="Next">
            <SkipForward size={18} />
          </button>
          <button className="text-text-muted hover:text-text-secondary transition-colors" aria-label="Repeat">
            <Repeat size={15} />
          </button>
        </div>

        {/* Progress bar — static stub */}
        <div className="w-full flex items-center gap-2.5">
          <span className="font-mono text-[11px] text-text-muted tabular-nums w-9 text-right">1:48</span>
          <div className="flex-1 h-1 bg-[#1a1a1a] rounded-full relative cursor-pointer group">
            <div
              className="absolute top-0 left-0 h-full bg-accent rounded-full"
              style={{ width: `${STUB_PROGRESS}%` }}
            />
            <div
              className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 bg-accent rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
              style={{ left: `${STUB_PROGRESS}%`, marginLeft: '-5px' }}
            />
          </div>
          <span className="font-mono text-[11px] text-text-muted tabular-nums w-9">5:18</span>
        </div>
      </div>

      {/* ── Right: volume + actions ───────────────────────────────────────── */}
      <div className="flex items-center gap-3 w-[200px] justify-end">
        <button className="text-text-muted hover:text-text-secondary transition-colors" aria-label="Add to playlist" title="Add to playlist">
          <ListPlus size={16} />
        </button>
        <button
          onClick={onExpand}
          className="text-text-muted hover:text-text-secondary transition-colors"
          aria-label="Full page player"
          title="Full page player"
        >
          <Maximize2 size={14} />
        </button>

        <div className="w-px h-4 bg-[#222] mx-1" />

        <Volume2 size={15} className="text-text-secondary flex-shrink-0" />

        {/* Volume slider — static stub */}
        <div className="w-20 h-1 bg-[#1a1a1a] rounded-full relative cursor-pointer group">
          <div
            className="absolute top-0 left-0 h-full bg-text-secondary rounded-full"
            style={{ width: `${STUB_VOLUME}%` }}
          />
          <div
            className="absolute top-1/2 -translate-y-1/2 w-2 h-2 bg-text-primary rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
            style={{ left: `${STUB_VOLUME}%`, marginLeft: '-4px' }}
          />
        </div>

        <button
          onClick={onMinimize}
          className="text-text-muted hover:text-text-secondary transition-colors ml-1"
          aria-label="Minimize player"
          title="Minimize"
        >
          <ChevronDown size={16} />
        </button>
      </div>
    </div>
  );
}
