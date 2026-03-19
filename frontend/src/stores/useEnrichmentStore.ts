/**
 * useEnrichmentStore — global enrichment pipeline state.
 *
 * Holds the full pipeline state so any component can read live enrichment
 * progress without owning a WebSocket connection.
 *
 * Write path: wsService routes WS events to handleProgress / handleComplete /
 *             handleStopped. App.tsx seeds initial state via setFromStatus on
 *             load. Start button calls reset() before runFull().
 *
 * Read path: components subscribe with selectors, e.g.:
 *   const running = useEnrichmentStore(s => s.running);
 *   const { workers, activeWorkers } = useEnrichmentStore();
 */
import { create } from 'zustand';
import type {
  EnrichmentPipelineStatus,
  EnrichmentWorkerStatus,
  WsEnrichmentProgress,
} from '../types';

interface EnrichmentStore {
  running: boolean;
  workers: Record<string, EnrichmentWorkerStatus>;
  activeWorkers: Set<string>;
  /** Epoch ms when current run started. Components compute elapsed from this. */
  startedAt: number | null;

  /** Called by wsService on enrichment_progress events. */
  handleProgress: (p: unknown) => void;
  /** Called by wsService on enrichment_complete events. */
  handleComplete: (p: unknown) => void;
  /** Called by wsService on enrichment_stopped events. */
  handleStopped: () => void;
  /** Seed state from the REST /enrich/status response on app load. */
  setFromStatus: (s: EnrichmentPipelineStatus) => void;
  /** Optimistic reset before starting a new run. */
  reset: () => void;
}

export const useEnrichmentStore = create<EnrichmentStore>((set) => ({
  running: false,
  workers: {},
  activeWorkers: new Set(),
  startedAt: null,

  handleProgress: (p) => {
    const { worker, found, not_found, errors, pending, running } = p as WsEnrichmentProgress;
    set(s => ({
      running,
      startedAt: s.startedAt ?? Date.now(),
      activeWorkers: new Set([...s.activeWorkers, worker]),
      workers: { ...s.workers, [worker]: { found, not_found, errors, pending } },
    }));
  },

  handleComplete: (p) => {
    const { workers } = p as { workers: Record<string, EnrichmentWorkerStatus> };
    set({ running: false, workers, activeWorkers: new Set(), startedAt: null });
  },

  handleStopped: () => {
    set({ running: false, activeWorkers: new Set(), startedAt: null });
  },

  setFromStatus: (s) => {
    set({
      running: s.running ?? false,
      workers: s.workers ?? {},
      startedAt: s.running && s.started_at ? new Date(s.started_at).getTime() : null,
    });
  },

  reset: () => set({
    running: true,
    workers: {},
    activeWorkers: new Set(),
    startedAt: Date.now(),
  }),
}));
