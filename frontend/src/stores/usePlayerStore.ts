/**
 * usePlayerStore — global audio player state.
 *
 * Scope: all playback state shared across PlayerBar, FullPagePlayer,
 * Library play buttons, and useAudioEngine.
 *
 * Why Zustand: player state needs to be accessible from the footer bar,
 * the full-page overlay, every library page, and the audio engine hook —
 * deep prop drilling would cross too many boundaries.
 *
 * PlayerTrack is a minimal representation of a lib_tracks row with just
 * enough data for the player UI. Resolved from lib_tracks at enqueue time.
 */
import { create } from 'zustand';

export type PlayerVisibility = 'hidden' | 'mini' | 'fullpage' | 'vinyl';

export interface PlayerTrack {
  id: string;
  title: string;
  artist: string;
  album: string;
  duration: number | null;
  thumb_url: string | null;
  source_platform: string;
  /** Optional quality metadata for the quality badge */
  codec?: string | null;
  bitrate?: number | null;
  bit_depth?: number | null;
  sample_rate?: number | null;
}

interface PlayerStore {
  // ── Visibility ──────────────────────────────────────────────────────────
  playerState: PlayerVisibility;

  // ── Playback ────────────────────────────────────────────────────────────
  isPlaying: boolean;
  currentTrack: PlayerTrack | null;
  queue: PlayerTrack[];
  queueIndex: number;

  // ── Position / duration (managed by useAudioEngine) ─────────────────────
  position: number;     // seconds
  duration: number;     // seconds
  volume: number;       // 0–1

  // ── Formatted strings for display (avoids recompute in render) ───────────
  formattedPosition: string;
  formattedDuration: string;

  // ── Shuffle / Repeat ─────────────────────────────────────────────────────
  shuffle: boolean;
  repeat: boolean;

  // ── Visibility actions ───────────────────────────────────────────────────
  setPlayerState: (state: PlayerVisibility) => void;
  show: () => void;
  hide: () => void;
  expand: () => void;
  minimize: () => void;
  showVinyl: () => void;

  // ── Playback actions ─────────────────────────────────────────────────────
  setIsPlaying: (playing: boolean) => void;
  togglePlayPause: () => void;

  // ── Queue actions ─────────────────────────────────────────────────────────
  playQueue: (tracks: PlayerTrack[]) => void;
  enqueueNext: (tracks: PlayerTrack[]) => void;
  nextTrack: () => void;
  prevTrack: () => void;
  playAt: (index: number) => void;
  clearQueue: () => void;
  toggleShuffle: () => void;
  toggleRepeat: () => void;

  // ── Position / volume (set by useAudioEngine) ────────────────────────────
  setPosition: (pos: number) => void;
  setDuration: (dur: number) => void;
  setFormattedPosition: (s: string) => void;
  setFormattedDuration: (s: string) => void;
  setVolume: (vol: number) => void;
}

export const usePlayerStore = create<PlayerStore>((set, get) => ({
  // ── Initial state ────────────────────────────────────────────────────────
  playerState: 'hidden',
  isPlaying: false,
  currentTrack: null,
  queue: [],
  queueIndex: -1,
  position: 0,
  duration: 0,
  volume: 0.8,
  formattedPosition: '0:00',
  formattedDuration: '0:00',
  shuffle: false,
  repeat: false,

  // ── Visibility ───────────────────────────────────────────────────────────
  setPlayerState: (playerState) => set({ playerState }),
  show:      () => set({ playerState: 'mini' }),
  hide:      () => set({ playerState: 'hidden', isPlaying: false }),
  expand:    () => set({ playerState: 'fullpage' }),
  minimize:  () => set({ playerState: 'mini' }),
  showVinyl: () => set({ playerState: 'vinyl' }),

  // ── Playback ─────────────────────────────────────────────────────────────
  setIsPlaying: (isPlaying) => set({ isPlaying }),
  togglePlayPause: () => set((s) => ({ isPlaying: !s.isPlaying })),

  // ── Queue ─────────────────────────────────────────────────────────────────
  playQueue: (tracks) => {
    if (!tracks.length) return;
    set({
      queue: tracks,
      queueIndex: 0,
      currentTrack: tracks[0],
      isPlaying: true,
      playerState: 'mini',
      position: 0,
      duration: 0,
      formattedPosition: '0:00',
      formattedDuration: '0:00',
    });
  },

  enqueueNext: (tracks) => {
    if (!tracks.length) return;
    const { queue, queueIndex } = get();
    const insertAt = queueIndex + 1;
    const newQueue = [
      ...queue.slice(0, insertAt),
      ...tracks,
      ...queue.slice(insertAt),
    ];
    set({ queue: newQueue });
  },

  nextTrack: () => {
    const { queue, queueIndex, shuffle, repeat } = get();
    if (!queue.length) return;
    let next: number;
    if (shuffle) {
      const candidates = queue.map((_, i) => i).filter((i) => i !== queueIndex);
      next = candidates.length
        ? candidates[Math.floor(Math.random() * candidates.length)]
        : queueIndex;
    } else {
      next = queueIndex + 1;
    }
    if (next >= queue.length) {
      if (repeat) {
        // Repeat: loop back to the start of the queue
        next = 0;
      } else {
        set({ isPlaying: false });
        return;
      }
    }
    set({
      queueIndex: next,
      currentTrack: queue[next],
      isPlaying: true,
      position: 0,
      formattedPosition: '0:00',
    });
  },

  prevTrack: () => {
    const { queue, queueIndex, position } = get();
    if (!queue.length) return;
    // More than 3s in → restart; at start of queue → restart
    if (position > 3 || queueIndex === 0) {
      set({ position: 0, formattedPosition: '0:00' });
      return;
    }
    const prev = queueIndex - 1;
    set({
      queueIndex: prev,
      currentTrack: queue[prev],
      isPlaying: true,
      position: 0,
      formattedPosition: '0:00',
    });
  },

  playAt: (index) => {
    const { queue } = get();
    if (index < 0 || index >= queue.length) return;
    set({
      queueIndex: index,
      currentTrack: queue[index],
      isPlaying: true,
      position: 0,
      formattedPosition: '0:00',
    });
  },

  clearQueue: () => set({
    queue: [],
    queueIndex: -1,
    currentTrack: null,
    isPlaying: false,
    position: 0,
    duration: 0,
    formattedPosition: '0:00',
    formattedDuration: '0:00',
  }),

  toggleShuffle: () => set((s) => ({ shuffle: !s.shuffle })),
  toggleRepeat:  () => set((s) => ({ repeat: !s.repeat })),

  setPosition: (position) => set({ position }),
  setDuration: (duration) => set({ duration }),
  setFormattedPosition: (formattedPosition) => set({ formattedPosition }),
  setFormattedDuration: (formattedDuration) => set({ formattedDuration }),
  setVolume: (volume) => set({ volume }),
}));
