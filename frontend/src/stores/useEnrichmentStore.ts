/**
 * useEnrichmentStore — global enrichment pipeline state.
 *
 * Scope: tracks whether the enrichment pipeline is running.
 * Consumers: ProcessingSignal (sidebar widget), Settings PipelineOrchestrator.
 *
 * Write path: useWebSocket handler calls setRunning() when enrichment WS events arrive.
 * Read path: any component can subscribe with useEnrichmentStore(s => s.isRunning).
 *
 * Not for local component state — keep useState for anything that doesn't
 * need to be shared across the component tree.
 */
import { create } from 'zustand';

interface EnrichmentStore {
  isRunning: boolean;
  setRunning: (running: boolean) => void;
}

export const useEnrichmentStore = create<EnrichmentStore>((set) => ({
  isRunning: false,
  setRunning: (running) => set({ isRunning: running }),
}));
