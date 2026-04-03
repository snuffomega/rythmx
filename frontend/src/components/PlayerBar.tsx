/**
 * PlayerBar - mini player bar fixed at the bottom of the app layout.
 *
 * Reads playback state from usePlayerStore.
 * Seek and volume changes are forwarded to useAudioEngine handlers via props.
 */
import {
  Play, Pause, SkipBack, SkipForward,
  Volume2, Repeat, Shuffle, ListPlus, Maximize2, Disc,
} from 'lucide-react';
import { useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { usePlayerStore } from '../stores/usePlayerStore';
import { AudioQualityBadge } from './common';
import { useImage } from '../hooks/useImage';
import { libraryBrowseApi } from '../services/api';

interface PlayerBarProps {
  isPlaying: boolean;
  onPlayPause: () => void;
  onExpand: () => void;
  onSeek: (seconds: number) => void;
  onVolumeChange: (vol: number) => void;
}

function TrackArt({ thumbUrl, title, artist }: { thumbUrl: string | null; title: string; artist: string }) {
  const resolved = useImage('album', title, artist);
  const src = thumbUrl ?? resolved ?? null;
  if (src) return <img src={src} alt="" className="w-12 h-12 object-cover rounded-sm flex-shrink-0" />;
  return (
    <div className="w-12 h-12 bg-[#1a1a1a] rounded-sm flex-shrink-0 flex items-center justify-center border border-[#222]">
      <Disc size={20} className="text-text-muted" />
    </div>
  );
}

export function PlayerBar({ isPlaying, onPlayPause, onExpand, onSeek, onVolumeChange }: PlayerBarProps) {
  const navigate = useNavigate();
  const [artistNavLoading, setArtistNavLoading] = useState(false);
  const {
    currentTrack,
    queue,
    formattedPosition,
    formattedDuration,
    position,
    duration,
    volume,
    shuffle,
    nextTrack,
    prevTrack,
    toggleShuffle,
  } = usePlayerStore();

  const progressRef = useRef<HTMLDivElement>(null);
  const volumeRef = useRef<HTMLDivElement>(null);

  const progressPct = duration > 0 ? (position / duration) * 100 : 0;

  function handleProgressClick(e: React.MouseEvent<HTMLDivElement>) {
    if (!progressRef.current || duration <= 0) return;
    const rect = progressRef.current.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    onSeek(pct * duration);
  }

  function handleVolumeClick(e: React.MouseEvent<HTMLDivElement>) {
    if (!volumeRef.current) return;
    const rect = volumeRef.current.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    onVolumeChange(pct);
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

  return (
    <div className="fixed bottom-0 left-0 right-0 z-30 h-[80px] bg-[#0a0a0a] border-t border-[#1a1a1a] flex items-center px-5 gap-5">
      <div className="flex items-center gap-3.5 w-[280px] min-w-0">
        <button
          onClick={onExpand}
          disabled={!currentTrack}
          className="rounded-sm transition-all hover:brightness-110 active:scale-95 disabled:opacity-60 disabled:cursor-default"
          aria-label="Open large player"
          title="Open large player"
        >
          <TrackArt
            thumbUrl={currentTrack?.thumb_url ?? null}
            title={currentTrack?.album ?? ''}
            artist={currentTrack?.artist ?? ''}
          />
        </button>
        <div className="min-w-0 flex-1">
          <p className="text-[15px] text-text-primary truncate leading-tight">
            {currentTrack?.title ?? 'Nothing playing'}
          </p>
          <button
            onClick={handleArtistClick}
            disabled={!currentTrack?.artist || artistNavLoading}
            className="font-mono text-[13px] text-text-secondary hover:text-accent truncate leading-tight mt-0.5 transition-colors text-left max-w-full disabled:cursor-default disabled:hover:text-text-secondary"
            title={currentTrack?.artist ? `Open ${currentTrack.artist}` : undefined}
          >
            {currentTrack?.artist ?? '-'}
          </button>
          {currentTrack && (
            <div className="mt-0.5">
              <AudioQualityBadge
                codec={currentTrack.codec}
                bitrate={currentTrack.bitrate}
                bit_depth={currentTrack.bit_depth}
                sample_rate={currentTrack.sample_rate}
              />
            </div>
          )}
        </div>
      </div>

      <div className="flex-1 flex flex-col items-center justify-center gap-1.5 max-w-[640px] mx-auto">
        <div className="flex items-center gap-5">
          <button
            onClick={toggleShuffle}
            className={`transition-colors ${shuffle ? 'text-accent' : 'text-text-muted hover:text-text-secondary'}`}
            aria-label="Shuffle"
          >
            <Shuffle size={15} />
          </button>
          <button
            onClick={prevTrack}
            className="text-text-secondary hover:text-text-primary transition-colors"
            aria-label="Previous"
            disabled={!currentTrack}
          >
            <SkipBack size={18} />
          </button>
          <button
            onClick={onPlayPause}
            aria-label={isPlaying ? 'Pause' : 'Play'}
            disabled={!currentTrack}
            className="w-9 h-9 rounded-full bg-accent hover:bg-accent/80 flex items-center justify-center transition-colors disabled:opacity-40"
          >
            {isPlaying
              ? <Pause size={16} className="text-black" />
              : <Play size={16} className="text-black ml-0.5" />}
          </button>
          <button
            onClick={nextTrack}
            className="text-text-secondary hover:text-text-primary transition-colors"
            aria-label="Next"
            disabled={!currentTrack}
          >
            <SkipForward size={18} />
          </button>
          <button className="text-text-muted hover:text-text-secondary transition-colors" aria-label="Repeat">
            <Repeat size={15} />
          </button>
        </div>

        <div className="w-full flex items-center gap-2.5">
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
              className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 bg-accent rounded-full opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
              style={{ left: `${progressPct}%`, marginLeft: '-5px' }}
            />
          </div>
          <span className="font-mono text-[11px] text-text-muted tabular-nums w-9">
            {formattedDuration}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-3 w-[200px] justify-end">
        {queue.length > 0 && (
          <span className="font-mono text-[11px] text-text-muted tabular-nums">
            {usePlayerStore.getState().queueIndex + 1}/{queue.length}
          </span>
        )}
        <button className="text-text-muted hover:text-text-secondary transition-colors" aria-label="Queue" title="Queue">
          <ListPlus size={16} />
        </button>
        <button
          onClick={onExpand}
          className="text-text-muted hover:text-text-secondary transition-colors"
          aria-label="Open large player"
          title="Open large player"
        >
          <Maximize2 size={14} />
        </button>

        <div className="w-px h-4 bg-[#222] mx-1" />

        <Volume2 size={15} className="text-text-secondary flex-shrink-0" />
        <div
          ref={volumeRef}
          onClick={handleVolumeClick}
          className="w-20 h-1 bg-[#1a1a1a] rounded-full relative cursor-pointer group"
        >
          <div
            className="absolute top-0 left-0 h-full bg-text-secondary rounded-full pointer-events-none"
            style={{ width: `${volume * 100}%` }}
          />
          <div
            className="absolute top-1/2 -translate-y-1/2 w-2 h-2 bg-text-primary rounded-full opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
            style={{ left: `${volume * 100}%`, marginLeft: '-4px' }}
          />
        </div>
      </div>
    </div>
  );
}
