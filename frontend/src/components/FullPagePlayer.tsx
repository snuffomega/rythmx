/**
 * FullPagePlayer — replaces main content area when playerState === 'fullpage'.
 *
 * Left panel: album art, track info, progress, controls, volume.
 * Right panel: queue list — click any item to jump to it.
 */
import {
  Play, Pause, SkipBack, SkipForward,
  Volume2, Repeat, Repeat1, Shuffle, ListPlus, Minimize2, Disc,
} from 'lucide-react';
import { useCallback, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { usePlayerStore } from '../stores/usePlayerStore';
// isPlaying is still a prop here (owned by __root → useAudioEngine) but
// repeat / shuffle / showVinyl are pure store actions, read directly.
import { AudioQualityBadge } from './common';
import { useImage } from '../hooks/useImage';
import { libraryPlaylistsApi } from '../services/api';
import { getImageUrl } from '../utils/imageUrl';
import { useToastStore } from '../stores/useToastStore';
import type { LibPlaylist } from '../types';

interface FullPagePlayerProps {
  isPlaying: boolean;
  onPlayPause: () => void;
  onMinimize: () => void;
  onSeek: (seconds: number) => void;
  onVolumeChange: (vol: number) => void;
}

function TrackArt({
  thumbUrl,
  thumbHash,
  title,
  artist,
}: {
  thumbUrl: string | null;
  thumbHash?: string | null;
  title: string;
  artist: string;
}) {
  const resolved = useImage('album', title, artist);
  const src = thumbUrl
    ? getImageUrl(thumbUrl, thumbHash ?? null)
    : (resolved ? getImageUrl(resolved) : null);
  if (src) return <img src={src} alt="" className="w-full max-w-[400px] aspect-square object-cover rounded border border-[#222]" />;
  return (
    <div className="w-full max-w-[400px] aspect-square bg-[#1a1a1a] rounded flex items-center justify-center border border-[#222]">
      <Disc size={80} className="text-[#333]" />
    </div>
  );
}

export function FullPagePlayer({ isPlaying, onPlayPause, onMinimize, onSeek, onVolumeChange }: FullPagePlayerProps) {
  const navigate = useNavigate();
  const toastSuccess = useToastStore(s => s.success);
  const toastError = useToastStore(s => s.error);
  const [playlistOptions, setPlaylistOptions] = useState<LibPlaylist[]>([]);
  const [playlistPickerOpen, setPlaylistPickerOpen] = useState(false);
  const [playlistPickerLoading, setPlaylistPickerLoading] = useState(false);
  const [playlistPickerSaving, setPlaylistPickerSaving] = useState(false);
  const [playlistPickerError, setPlaylistPickerError] = useState<string | null>(null);
  const [playlistTrack, setPlaylistTrack] = useState<{ id: string; title: string } | null>(null);
  const [selectedPlaylistId, setSelectedPlaylistId] = useState('');

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
    repeatMode,
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
  const effectiveDuration = duration > 0 ? duration : (currentTrack?.duration ?? 0);

  const openPlaylistPicker = useCallback(async (track: { id: string; title: string }) => {
    setPlaylistTrack(track);
    setPlaylistPickerOpen(true);
    setPlaylistPickerLoading(true);
    setPlaylistPickerSaving(false);
    setPlaylistPickerError(null);
    try {
      const list = await libraryPlaylistsApi.list();
      setPlaylistOptions(list);
      setSelectedPlaylistId(list[0]?.id ?? '');
      if (list.length === 0) {
        setPlaylistPickerError('No playlists found. Sync or create one in Library > Playlists first.');
      }
    } catch (err) {
      setPlaylistOptions([]);
      setSelectedPlaylistId('');
      setPlaylistPickerError(err instanceof Error ? err.message : 'Failed to load playlists');
    } finally {
      setPlaylistPickerLoading(false);
    }
  }, []);

  const closePlaylistPicker = useCallback(() => {
    setPlaylistPickerOpen(false);
    setPlaylistPickerLoading(false);
    setPlaylistPickerSaving(false);
    setPlaylistPickerError(null);
    setPlaylistTrack(null);
    setSelectedPlaylistId('');
  }, []);

  const confirmAddToPlaylist = useCallback(async () => {
    if (!playlistTrack || !selectedPlaylistId) {
      return;
    }
    setPlaylistPickerSaving(true);
    setPlaylistPickerError(null);
    try {
      const result = await libraryPlaylistsApi.addTracks(selectedPlaylistId, [playlistTrack.id]);
      const selected = playlistOptions.find(p => p.id === selectedPlaylistId);
      toastSuccess(
        `Added "${playlistTrack.title}" to "${selected?.name ?? selectedPlaylistId}" (${result.track_count} tracks)`
      );
      closePlaylistPicker();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to add track to playlist';
      setPlaylistPickerError(msg);
      toastError(msg);
    } finally {
      setPlaylistPickerSaving(false);
    }
  }, [playlistTrack, selectedPlaylistId, playlistOptions, toastSuccess, toastError, closePlaylistPicker]);

  function handleProgressClick(e: React.MouseEvent<HTMLDivElement>) {
    if (!progressRef.current || effectiveDuration <= 0) return;
    const rect = progressRef.current.getBoundingClientRect();
    const pct  = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    onSeek(pct * effectiveDuration);
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
          <TrackArt
            thumbUrl={currentTrack?.thumb_url ?? null}
            thumbHash={currentTrack?.thumb_hash ?? null}
            title={currentTrack?.album ?? ''}
            artist={currentTrack?.artist ?? ''}
          />
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
              : <Play  size={22} className="text-black" />}
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
            className={`transition-colors ${repeatMode !== 'off' ? 'text-accent' : 'text-text-muted hover:text-text-secondary'}`}
            aria-label={
              repeatMode === 'off' ? 'Repeat off'
                : repeatMode === 'all' ? 'Repeat all'
                  : 'Repeat one'
            }
            title={
              repeatMode === 'off' ? 'Repeat off'
                : repeatMode === 'all' ? 'Repeat all'
                  : 'Repeat one'
            }
          >
            {repeatMode === 'one' ? <Repeat1 size={18} /> : <Repeat size={18} />}
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
                  <div className="group px-5 py-3 flex items-center gap-3 hover:bg-[#111] transition-colors">
                    <button
                      onClick={() => playAt(i)}
                      className={`flex-1 min-w-0 text-left flex items-center gap-3 ${
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
                    <button
                      onClick={() => openPlaylistPicker(track)}
                      className="text-text-muted opacity-0 group-hover:opacity-100 hover:text-accent transition-all"
                      title="Add to playlist"
                      aria-label={`Add ${track.title} to playlist`}
                    >
                      <ListPlus size={14} />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {playlistPickerOpen && (
        <div className="fixed inset-0 z-40 bg-black/60 flex items-center justify-center p-4">
          <div className="w-full max-w-md bg-[#0d0d0d] border border-[#2a2a2a] p-4">
            <h3 className="text-sm font-semibold text-text-primary">Add To Playlist</h3>
            <p className="text-xs text-text-muted mt-1 truncate">
              {playlistTrack ? `Track: ${playlistTrack.title}` : 'Select a playlist'}
            </p>

            {playlistPickerLoading ? (
              <div className="flex items-center gap-2 text-xs text-text-muted mt-4">
                <Disc size={12} className="animate-spin" />
                Loading playlists...
              </div>
            ) : (
              <>
                <div className="mt-4">
                  <label className="block text-[10px] font-mono text-text-muted uppercase tracking-wider mb-1.5">
                    Playlist
                  </label>
                  <select
                    value={selectedPlaylistId}
                    onChange={e => setSelectedPlaylistId(e.target.value)}
                    className="w-full bg-[#111] border border-[#2a2a2a] text-text-primary text-sm px-3 py-2 focus:outline-none focus:border-accent"
                    disabled={playlistOptions.length === 0 || playlistPickerSaving}
                  >
                    {playlistOptions.length === 0 ? (
                      <option value="">No playlists available</option>
                    ) : (
                      playlistOptions.map(pl => (
                        <option key={pl.id} value={pl.id}>
                          {pl.name} ({pl.track_count} tracks)
                        </option>
                      ))
                    )}
                  </select>
                </div>

                {playlistPickerError && (
                  <p className="text-xs text-danger mt-3">{playlistPickerError}</p>
                )}
              </>
            )}

            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                onClick={closePlaylistPicker}
                disabled={playlistPickerSaving}
                className="px-3 py-1.5 text-xs border border-[#333] text-text-muted hover:text-text-primary transition-colors disabled:opacity-40"
              >
                Cancel
              </button>
              <button
                onClick={() => navigate({ to: '/library/playlists' })}
                disabled={playlistPickerSaving}
                className="px-3 py-1.5 text-xs border border-[#333] text-text-muted hover:text-text-primary transition-colors disabled:opacity-40"
              >
                Open Playlists
              </button>
              <button
                onClick={confirmAddToPlaylist}
                disabled={playlistPickerLoading || playlistPickerSaving || !playlistTrack || !selectedPlaylistId}
                className="px-3 py-1.5 text-xs bg-accent text-black hover:bg-accent/80 transition-colors disabled:opacity-40"
              >
                {playlistPickerSaving ? 'Adding...' : 'Add'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
