import { create } from 'zustand';
import type { Settings } from '../types';

interface SettingsStore {
  fetchEnabled: boolean;
  initialized: boolean;
  setFetchEnabled: (v: boolean) => void;
  initFromApi: (settings: Settings) => void;
}

export const useSettingsStore = create<SettingsStore>((set) => ({
  fetchEnabled: false,
  initialized: false,
  setFetchEnabled: (fetchEnabled) => set({ fetchEnabled }),
  initFromApi: (settings) => set({
    fetchEnabled: settings.fetch_enabled ?? false,
    initialized: true,
  }),
}));
