/**
 * usePlayerStore — global audio player state.
 *
 * Scope: player visibility + playback state shared across PlayerBar,
 * FullPagePlayer, Library play buttons, and any future queue-aware component.
 *
 * Why Zustand: player state (queue, current track, playback position, shuffle)
 * needs to be accessible from PlayerBar (footer), FullPagePlayer (overlay),
 * Library play buttons (main content), and CruiseControl (sidebar). Deep
 * prop drilling would require passing through App → every page → every card.
 *
 * Current fields cover the stub player. Extend this store when the real audio
 * engine (Phase R&D) is implemented — add currentTrack, queue, position, etc.
 *
 * Not for local component state — keep useState for anything that doesn't
 * need to be shared across the component tree.
 */
import { create } from 'zustand';

export type PlayerVisibility = 'hidden' | 'mini' | 'fullpage';

interface PlayerStore {
  playerState: PlayerVisibility;
  isPlaying: boolean;
  setPlayerState: (state: PlayerVisibility) => void;
  setIsPlaying: (playing: boolean) => void;
  togglePlayPause: () => void;
  show: () => void;
  hide: () => void;
  expand: () => void;
  minimize: () => void;
}

export const usePlayerStore = create<PlayerStore>((set) => ({
  playerState: 'hidden',
  isPlaying: false,
  setPlayerState: (playerState) => set({ playerState }),
  setIsPlaying: (isPlaying) => set({ isPlaying }),
  togglePlayPause: () => set((s) => ({ isPlaying: !s.isPlaying })),
  show: () => set({ playerState: 'mini', isPlaying: true }),
  hide: () => set({ playerState: 'hidden' }),
  expand: () => set({ playerState: 'fullpage' }),
  minimize: () => set({ playerState: 'mini' }),
}));
