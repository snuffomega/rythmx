import { create } from 'zustand';
import { useToastStore } from './useToastStore';

export type PlayerVisibility = 'hidden' | 'mini' | 'fullpage' | 'vinyl';
export type RepeatMode = 'off' | 'all' | 'one';

export interface PlayerTrack {
  id: string;
  title: string;
  artist: string;
  album: string;
  duration: number | null;
  thumb_url: string | null;
  thumb_hash?: string | null;
  source_platform: string;
  codec?: string | null;
  bitrate?: number | null;
  bit_depth?: number | null;
  sample_rate?: number | null;
}

interface PlayerStore {
  playerState: PlayerVisibility;

  isPlaying: boolean;
  currentTrack: PlayerTrack | null;
  queue: PlayerTrack[];
  queueIndex: number;

  position: number;
  duration: number;
  volume: number;

  formattedPosition: string;
  formattedDuration: string;

  shuffle: boolean;
  repeatMode: RepeatMode;

  setPlayerState: (state: PlayerVisibility) => void;
  show: () => void;
  hide: () => void;
  expand: () => void;
  minimize: () => void;
  showVinyl: () => void;

  setIsPlaying: (playing: boolean) => void;
  togglePlayPause: () => void;

  playQueue: (tracks: PlayerTrack[]) => void;
  enqueueNext: (tracks: PlayerTrack[]) => void;
  addToQueue: (tracks: PlayerTrack[]) => void;
  nextTrack: () => void;
  prevTrack: () => void;
  playAt: (index: number) => void;
  removeFromQueue: (index: number) => void;
  moveQueueItem: (fromIndex: number, toIndex: number) => void;
  clearQueue: () => void;
  toggleShuffle: () => void;
  toggleRepeat: () => void;

  setPosition: (pos: number) => void;
  setDuration: (dur: number) => void;
  setFormattedPosition: (s: string) => void;
  setFormattedDuration: (s: string) => void;
  setVolume: (vol: number) => void;
}

function clampIndex(index: number, max: number) {
  return Math.max(0, Math.min(index, max));
}

export const usePlayerStore = create<PlayerStore>((set, get) => ({
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
  repeatMode: 'off',

  setPlayerState: (playerState) => set({ playerState }),
  show: () => set({ playerState: 'mini' }),
  hide: () => set({ playerState: 'hidden', isPlaying: false }),
  expand: () => set({ playerState: 'vinyl' }),
  minimize: () => set({ playerState: 'mini' }),
  showVinyl: () => set({ playerState: 'vinyl' }),

  setIsPlaying: (isPlaying) => set({ isPlaying }),
  togglePlayPause: () => set((s) => ({ isPlaying: !s.isPlaying })),

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

  addToQueue: (tracks) => {
    if (!tracks.length) return;
    const { queue, currentTrack, queueIndex } = get();
    const toastSuccess = useToastStore.getState().success;

    if (!queue.length || !currentTrack || queueIndex < 0) {
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
      toastSuccess(
        tracks.length === 1
          ? `Queued "${tracks[0].title}" and started playback`
          : `Queued ${tracks.length} tracks and started playback`
      );
      return;
    }

    set({ queue: [...queue, ...tracks] });
    toastSuccess(
      tracks.length === 1
        ? `Added "${tracks[0].title}" to queue`
        : `Added ${tracks.length} tracks to queue`
    );
  },

  nextTrack: () => {
    const { queue, queueIndex, shuffle, repeatMode } = get();
    if (!queue.length) return;

    if (repeatMode === 'one' && queueIndex >= 0 && queueIndex < queue.length) {
      set({
        currentTrack: queue[queueIndex],
        isPlaying: true,
        position: 0,
        formattedPosition: '0:00',
      });
      return;
    }

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
      if (repeatMode === 'all') {
        next = 0;
      } else {
        set({
          isPlaying: false,
          currentTrack: null,
          queueIndex: -1,
          position: 0,
          duration: 0,
          formattedPosition: '0:00',
          formattedDuration: '0:00',
        });
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

  removeFromQueue: (index) => {
    const { queue, queueIndex, currentTrack, isPlaying } = get();
    if (index < 0 || index >= queue.length) return;

    const newQueue = queue.filter((_, i) => i !== index);
    if (!newQueue.length) {
      set({
        queue: [],
        queueIndex: -1,
        currentTrack: null,
        isPlaying: false,
        position: 0,
        duration: 0,
        formattedPosition: '0:00',
        formattedDuration: '0:00',
      });
      return;
    }

    if (queueIndex < 0 || queueIndex >= queue.length) {
      set({ queue: newQueue, queueIndex: -1, currentTrack: null });
      return;
    }

    if (index === queueIndex) {
      const nextIndex = clampIndex(index, newQueue.length - 1);
      set({
        queue: newQueue,
        queueIndex: nextIndex,
        currentTrack: newQueue[nextIndex],
        isPlaying,
        position: 0,
        formattedPosition: '0:00',
      });
      return;
    }

    const adjustedIndex = index < queueIndex ? queueIndex - 1 : queueIndex;
    const nextIndex = clampIndex(adjustedIndex, newQueue.length - 1);
    set({
      queue: newQueue,
      queueIndex: nextIndex,
      currentTrack: currentTrack ?? newQueue[nextIndex],
    });
  },

  moveQueueItem: (fromIndex, toIndex) => {
    const { queue, queueIndex, currentTrack } = get();
    if (
      fromIndex < 0 || fromIndex >= queue.length ||
      toIndex < 0 || toIndex >= queue.length ||
      fromIndex === toIndex
    ) {
      return;
    }

    const newQueue = [...queue];
    const [moved] = newQueue.splice(fromIndex, 1);
    newQueue.splice(toIndex, 0, moved);

    let nextQueueIndex = queueIndex;
    if (fromIndex === queueIndex) {
      nextQueueIndex = toIndex;
    } else if (fromIndex < queueIndex && toIndex >= queueIndex) {
      nextQueueIndex = queueIndex - 1;
    } else if (fromIndex > queueIndex && toIndex <= queueIndex) {
      nextQueueIndex = queueIndex + 1;
    }

    set({
      queue: newQueue,
      queueIndex: nextQueueIndex,
      currentTrack: (nextQueueIndex >= 0 && nextQueueIndex < newQueue.length)
        ? newQueue[nextQueueIndex]
        : currentTrack,
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
  toggleRepeat: () => set((s) => ({
    repeatMode: s.repeatMode === 'off'
      ? 'all'
      : s.repeatMode === 'all'
        ? 'one'
        : 'off',
  })),

  setPosition: (position) => set({ position }),
  setDuration: (duration) => set({ duration }),
  setFormattedPosition: (formattedPosition) => set({ formattedPosition }),
  setFormattedDuration: (formattedDuration) => set({ formattedDuration }),
  setVolume: (volume) => set({ volume }),
}));
