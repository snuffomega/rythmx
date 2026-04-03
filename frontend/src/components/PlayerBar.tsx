/**
 * PlayerBar - mini player bar fixed at the bottom of the app layout.
 */
import {
  Play, Pause, SkipBack, SkipForward,
  Volume2, Repeat, Shuffle, ListPlus, Maximize2, Disc, Star,
} from 'lucide-react';
import { useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { usePlayerStore } from '../stores/usePlayerStore';
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
  if (src) return <img src={src} alt="" className="w-16 h-16 object-cover rounded-sm flex-shrink-0" />;
  return (
    <div className="w-16 h-16 bg-[#1a1a1a] rounded-sm flex-shrink-0 flex items-center justify-center border border-[#222]">
      <Disc size={24} className="text-text-muted" />
    </div>
  );
}

export function PlayerBar({ isPlaying, onPlayPause, onExpand, onSeek, onVolumeChange }: PlayerBarProps) {
  const navigate = useNavigate();
  const [artistNavLoading, setArtistNavLoading] = useState(false);
  const [ratingByTrack, setRatingByTrack] = useState<Record<string, number>>({});
  const [hoverRating, setHoverRating] = useState(0);
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
  const currentRating = currentTrack ? ratingByTrack[currentTrack.id] ?? 0 : 0;
  const activeRating = hoverRating || currentRating;

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

  return (
    <div className="fixed bottom-0 left-0 right-0 z-30 h-[96px] bg-[#0a0a0a] border-t border-[#1a1a1a]">
      <div
        ref={progressRef}
        onClick={handleProgressClick}
        className="absolute top-0 left-0 right-0 h-[4px] bg-[#1a1a1a] cursor-pointer"
      >
        <div className="absolute top-0 left-0 h-full bg-accent" style={{ width: `${progressPct}%` }} />
      </div>

      <div className="h-full pt-[8px] px-6 flex items-center gap-6">
        <div className="flex items-center gap-4 w-[360px] min-w-0">
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
            <p className="text-[16px] text-text-primary truncate leading-tight">
              {currentTrack?.title ?? 'Nothing playing'}
            </p>
            <button
              onClick={handleArtistClick}
              disabled={!currentTrack?.artist || artistNavLoading}
              className="font-mono text-[14px] text-text-secondary hover:text-accent truncate leading-tight mt-0.5 transition-colors text-left max-w-full disabled:cursor-default disabled:hover:text-text-secondary"
              title={currentTrack?.artist ? `Open ${currentTrack.artist}` : undefined}
            >
              {currentTrack?.artist ?? '-'}
            </button>
          </div>
        </div>

        <div className="flex-1 flex items-center justify-center gap-4">
          <span className="font-mono text-[12px] text-text-muted tabular-nums">
            {formattedPosition} / {formattedDuration}
          </span>
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
          <button className="text-text-muted hover:text-text-secondary transition-colors" aria-label="Repeat">
            <Repeat size={17} />
          </button>
          <div className="flex items-center gap-0.5" onMouseLeave={() => setHoverRating(0)}>
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
                  className={star <= activeRating ? 'text-accent' : 'text-text-muted'}
                  fill={star <= activeRating ? 'currentColor' : 'none'}
                />
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-3 w-[240px] justify-end">
          {queue.length > 0 && (
            <span className="font-mono text-[12px] text-text-muted tabular-nums">
              {usePlayerStore.getState().queueIndex + 1}/{queue.length}
            </span>
          )}
          <button className="text-text-muted hover:text-text-secondary transition-colors" aria-label="Queue" title="Queue">
            <ListPlus size={18} />
          </button>
          <button
            onClick={onExpand}
            className="text-text-muted hover:text-text-secondary transition-colors"
            aria-label="Open large player"
            title="Open large player"
          >
            <Maximize2 size={16} />
          </button>
          <div className="w-px h-5 bg-[#222] mx-1" />
          <Volume2 size={17} className="text-text-secondary flex-shrink-0" />
          <div
            ref={volumeRef}
            onClick={handleVolumeClick}
            className="w-24 h-[3px] bg-[#1a1a1a] rounded-full relative cursor-pointer group"
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
    </div>
  );
}
