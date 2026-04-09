import { create } from 'zustand';

type ForgePipelineName = 'new_music' | 'custom_discovery' | 'fetch';

export interface ForgePipelineState {
  running: boolean;
  runId: string | null;
  stage: string | null;
  processed: number;
  total: number;
  message: string | null;
  error: string | null;
  completedAt: number | null;
}

interface ForgePipelineStore {
  pipelines: Record<ForgePipelineName, ForgePipelineState>;
  handleProgress: (payload: unknown) => void;
  handleComplete: (payload: unknown) => void;
  handleError: (payload: unknown) => void;
  resetPipeline: (pipeline: ForgePipelineName) => void;
}

const DEFAULT_PIPELINE_STATE: ForgePipelineState = {
  running: false,
  runId: null,
  stage: null,
  processed: 0,
  total: 0,
  message: null,
  error: null,
  completedAt: null,
};

function parsePipeline(raw: unknown): ForgePipelineName | null {
  return raw === 'new_music' || raw === 'custom_discovery' || raw === 'fetch' ? raw : null;
}

function toNumber(value: unknown, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

export const useForgePipelineStore = create<ForgePipelineStore>((set) => ({
  pipelines: {
    new_music: { ...DEFAULT_PIPELINE_STATE },
    custom_discovery: { ...DEFAULT_PIPELINE_STATE },
    fetch: { ...DEFAULT_PIPELINE_STATE },
  },

  handleProgress: (payload) => {
    const p = payload as Record<string, unknown>;
    const pipeline = parsePipeline(p.pipeline);
    if (!pipeline) return;

    set((s) => ({
      pipelines: {
        ...s.pipelines,
        [pipeline]: {
          ...s.pipelines[pipeline],
          running: true,
          runId: String(p.run_id ?? ''),
          stage: String(p.stage ?? ''),
          processed: Math.max(0, toNumber(p.processed, s.pipelines[pipeline].processed)),
          total: Math.max(0, toNumber(p.total, s.pipelines[pipeline].total)),
          message: String(p.message ?? ''),
          error: null,
          completedAt: null,
        },
      },
    }));
  },

  handleComplete: (payload) => {
    const p = payload as Record<string, unknown>;
    const pipeline = parsePipeline(p.pipeline);
    if (!pipeline) return;

    set((s) => ({
      pipelines: {
        ...s.pipelines,
        [pipeline]: {
          ...s.pipelines[pipeline],
          running: false,
          stage: 'done',
          message: 'Completed',
          error: null,
          completedAt: Date.now(),
        },
      },
    }));
  },

  handleError: (payload) => {
    const p = payload as Record<string, unknown>;
    const pipeline = parsePipeline(p.pipeline);
    if (!pipeline) return;

    set((s) => ({
      pipelines: {
        ...s.pipelines,
        [pipeline]: {
          ...s.pipelines[pipeline],
          running: false,
          error: String(p.message ?? 'Pipeline failed'),
          completedAt: Date.now(),
        },
      },
    }));
  },

  resetPipeline: (pipeline) => {
    set((s) => ({
      pipelines: {
        ...s.pipelines,
        [pipeline]: { ...DEFAULT_PIPELINE_STATE },
      },
    }));
  },
}));
