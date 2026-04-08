/**
 * useEnrichmentStore - global enrichment pipeline state.
 */
import { create } from 'zustand';
import type {
  EnrichmentLastRun,
  EnrichmentPipelineStatus,
  EnrichmentSubstepStatus,
  EnrichmentSubsteps,
  EnrichmentWorkerStatus,
  WsEnrichmentComplete,
  WsEnrichmentPhase,
  WsEnrichmentProgress,
  WsEnrichmentStopped,
  WsEnrichmentSubstep,
} from '../types';

const DEFAULT_SUBSTEPS: EnrichmentSubsteps = {
  ownership_sync: 'pending',
  normalize_titles: 'pending',
  missing_counts: 'pending',
  canonical: 'pending',
};

interface EnrichmentStore {
  running: boolean;
  workers: Record<string, EnrichmentWorkerStatus>;
  activeWorkers: Set<string>;
  startedAt: number | null;
  phase: string | null;
  substeps: EnrichmentSubsteps;
  lastRun: EnrichmentLastRun | null;

  handleProgress: (p: unknown) => void;
  handleComplete: (p: unknown) => void;
  handleStopped: (p?: unknown) => void;
  handlePhase: (p: unknown) => void;
  handleSubstep: (p: unknown) => void;
  updateSubstep: (substep: string, status: EnrichmentSubstepStatus) => void;
  resetSubsteps: () => void;
  setLastRun: (lastRun: EnrichmentLastRun | null) => void;
  setFromStatus: (s: EnrichmentPipelineStatus) => void;
  reset: () => void;
}

export const useEnrichmentStore = create<EnrichmentStore>((set) => ({
  running: false,
  workers: {},
  activeWorkers: new Set(),
  startedAt: null,
  phase: null,
  substeps: { ...DEFAULT_SUBSTEPS },
  lastRun: null,

  handleProgress: (p) => {
    const { worker, found, not_found, errors, pending, running } = p as WsEnrichmentProgress;
    set((s) => ({
      running,
      startedAt: s.startedAt ?? Date.now(),
      activeWorkers: new Set([...s.activeWorkers, worker]),
      workers: { ...s.workers, [worker]: { found, not_found, errors, pending } },
    }));
  },

  handleComplete: (p) => {
    const { workers, last_run } = p as WsEnrichmentComplete;
    set({
      running: false,
      workers,
      activeWorkers: new Set(),
      startedAt: null,
      phase: null,
      lastRun: last_run ?? null,
    });
  },

  handleStopped: (p) => {
    const payload = (p ?? {}) as WsEnrichmentStopped;
    set({
      running: false,
      activeWorkers: new Set(),
      startedAt: null,
      phase: null,
      lastRun: payload.last_run ?? null,
    });
  },

  handlePhase: (p) => {
    const { phase } = p as WsEnrichmentPhase;
    set((s) => ({
      phase,
      substeps: phase === 'sync' ? { ...DEFAULT_SUBSTEPS } : s.substeps,
    }));
  },

  handleSubstep: (p) => {
    const { substep, status } = p as WsEnrichmentSubstep;
    set((s) => ({
      substeps: {
        ...s.substeps,
        [substep]: status,
      } as EnrichmentSubsteps,
    }));
  },

  updateSubstep: (substep, status) => {
    set((s) => ({
      substeps: {
        ...s.substeps,
        [substep]: status,
      } as EnrichmentSubsteps,
    }));
  },

  resetSubsteps: () => set({ substeps: { ...DEFAULT_SUBSTEPS } }),

  setLastRun: (lastRun) => set({ lastRun }),

  setFromStatus: (s) => {
    const parsedStartedAt = s.running && s.started_at ? new Date(s.started_at).getTime() : null;
    set({
      running: s.running ?? false,
      workers: s.workers ?? {},
      startedAt: parsedStartedAt && !Number.isNaN(parsedStartedAt) ? parsedStartedAt : null,
      phase: s.phase ?? null,
      substeps: { ...DEFAULT_SUBSTEPS, ...(s.substeps ?? {}) },
      lastRun: s.last_run ?? null,
    });
  },

  reset: () => set((s) => ({
    running: true,
    workers: s.workers,
    activeWorkers: new Set(),
    startedAt: Date.now(),
    phase: null,
    substeps: { ...DEFAULT_SUBSTEPS },
  })),
}));

