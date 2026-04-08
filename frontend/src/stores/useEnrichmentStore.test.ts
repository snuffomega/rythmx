import { useEnrichmentStore } from './useEnrichmentStore';

describe('useEnrichmentStore substeps + lastRun', () => {
  beforeEach(() => {
    useEnrichmentStore.getState().reset();
    useEnrichmentStore.getState().resetSubsteps();
    useEnrichmentStore.getState().setLastRun(null);
    useEnrichmentStore.setState({ running: false, phase: null, startedAt: null });
  });

  it('initializes substeps as pending', () => {
    const state = useEnrichmentStore.getState();
    expect(state.substeps.ownership_sync).toBe('pending');
    expect(state.substeps.normalize_titles).toBe('pending');
    expect(state.substeps.missing_counts).toBe('pending');
    expect(state.substeps.canonical).toBe('pending');
  });

  it('updateSubstep updates one substep only', () => {
    const store = useEnrichmentStore.getState();
    store.updateSubstep('ownership_sync', 'running');
    const state = useEnrichmentStore.getState();
    expect(state.substeps.ownership_sync).toBe('running');
    expect(state.substeps.normalize_titles).toBe('pending');
  });

  it('resetSubsteps returns all substeps to pending', () => {
    const store = useEnrichmentStore.getState();
    store.updateSubstep('ownership_sync', 'completed');
    store.updateSubstep('canonical', 'running');
    store.resetSubsteps();
    const state = useEnrichmentStore.getState();
    expect(state.substeps.ownership_sync).toBe('pending');
    expect(state.substeps.canonical).toBe('pending');
  });

  it('setLastRun stores summary payload', () => {
    const run = {
      started_at: '2026-04-06T14:00:00Z',
      ended_at: '2026-04-06T14:14:32Z',
      duration_s: 872,
      outcome: 'completed' as const,
      enriched: 847,
      not_found: 153,
    };
    useEnrichmentStore.getState().setLastRun(run);
    expect(useEnrichmentStore.getState().lastRun).toEqual(run);
  });
});

