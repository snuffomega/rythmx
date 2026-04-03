/**
 * useAudioEngine — manages the HTML5 Audio element for playback.
 *
 * Architecture:
 *   - Single Audio instance held in a ref (not state) to avoid re-renders on tick
 *   - Playback state is written to usePlayerStore so all components stay in sync
 *   - Stream URL: /api/v1/library/tracks/{id}/stream?api_key={key}
 *     (api_key as query param because <audio> cannot send custom headers)
 *   - Range requests: handled transparently by the browser once Accept-Ranges
 *     is set on the proxy response (enables seeking)
 *   - MediaSession API: wires browser/OS media controls (lock screen, headphones)
 *
 * Usage:
 *   Call useAudioEngine() once in __root.tsx. It attaches listeners and returns
 *   playback control functions used by PlayerBar and FullPagePlayer.
 */
import { useEffect, useRef, useCallback } from 'react';
import { usePlayerStore } from '../stores/usePlayerStore';
import { getApiKey } from '../services/api';

function buildStreamUrl(trackId: string): string {
  const key = getApiKey();
  const params = key ? `?api_key=${encodeURIComponent(key)}` : '';
  return `/api/v1/library/tracks/${trackId}/stream${params}`;
}

function formatDuration(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export function useAudioEngine() {
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const {
    currentTrack,
    queue,
    isPlaying,
    setIsPlaying,
    setPosition,
    setDuration,
    nextTrack,
    setFormattedPosition,
    setFormattedDuration,
    setVolume: setStoreVolume,
  } = usePlayerStore();

  // Create the Audio element once
  useEffect(() => {
    if (!audioRef.current) {
      audioRef.current = new Audio();
      audioRef.current.preload = 'metadata';
      audioRef.current.volume = usePlayerStore.getState().volume;
    }
    const audio = audioRef.current;

    const onTimeUpdate = () => {
      const pos = audio.currentTime;
      setPosition(pos);
      setFormattedPosition(formatDuration(pos));
    };

    const onDurationChange = () => {
      const dur = audio.duration;
      if (isFinite(dur)) {
        setDuration(dur);
        setFormattedDuration(formatDuration(dur));
      }
    };

    const onEnded = () => {
      nextTrack();
    };

    const onPause = () => setIsPlaying(false);
    const onPlay  = () => setIsPlaying(true);

    audio.addEventListener('timeupdate', onTimeUpdate);
    audio.addEventListener('durationchange', onDurationChange);
    audio.addEventListener('ended', onEnded);
    audio.addEventListener('pause', onPause);
    audio.addEventListener('play', onPlay);

    return () => {
      audio.removeEventListener('timeupdate', onTimeUpdate);
      audio.removeEventListener('durationchange', onDurationChange);
      audio.removeEventListener('ended', onEnded);
      audio.removeEventListener('pause', onPause);
      audio.removeEventListener('play', onPlay);
    };
  }, [setIsPlaying, setPosition, setDuration, setFormattedPosition, setFormattedDuration, nextTrack]);

  // Load and play when currentTrack changes
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (!currentTrack) {
      audio.pause();
      audio.src = '';
      return;
    }
    audio.src = buildStreamUrl(currentTrack.id);
    audio.load();
    audio.play().catch(() => {
      // Autoplay blocked — user will need to click play
      setIsPlaying(false);
    });

    // MediaSession API — wires OS/browser media controls
    if ('mediaSession' in navigator) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: currentTrack.title,
        artist: currentTrack.artist,
        album: currentTrack.album,
        artwork: currentTrack.thumb_url
          ? [{ src: currentTrack.thumb_url, sizes: '512x512', type: 'image/jpeg' }]
          : [],
      });
      navigator.mediaSession.setActionHandler('play',           () => audio.play());
      navigator.mediaSession.setActionHandler('pause',          () => audio.pause());
      navigator.mediaSession.setActionHandler('previoustrack',  () => usePlayerStore.getState().prevTrack());
      navigator.mediaSession.setActionHandler('nexttrack',      () => usePlayerStore.getState().nextTrack());
      navigator.mediaSession.setActionHandler('seekto', (details) => {
        if (details.seekTime != null) {
          audio.currentTime = details.seekTime;
        }
      });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentTrack?.id]);

  // Sync isPlaying state → actual audio element
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !currentTrack) return;
    if (isPlaying && audio.paused) {
      audio.play().catch(() => setIsPlaying(false));
    } else if (!isPlaying && !audio.paused) {
      audio.pause();
    }
  }, [isPlaying, currentTrack, setIsPlaying]);

  // Seek handler for the scrubber
  const seek = useCallback((seconds: number) => {
    const audio = audioRef.current;
    if (audio && isFinite(audio.duration)) {
      audio.currentTime = Math.max(0, Math.min(seconds, audio.duration));
    }
  }, []);

  // Volume handler
  const setVolume = useCallback((vol: number) => {
    const clamped = Math.max(0, Math.min(1, vol));
    if (audioRef.current) {
      audioRef.current.volume = clamped;
    }
    setStoreVolume(clamped);
  }, [setStoreVolume]);

  return { seek, setVolume };
}
