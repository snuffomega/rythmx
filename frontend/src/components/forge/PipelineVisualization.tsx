import { AlertCircle, CheckCircle, Circle, Loader2 } from 'lucide-react';

const PIPELINE_STEPS = [
  { label: 'Poll History' },
  { label: 'Resolve Artists' },
  { label: 'Find New Releases' },
  { label: 'Check Library' },
  { label: 'Build', cruiseOnly: true },
  { label: 'Queue Tracks', cruiseOnly: true },
  { label: 'Create & Publish' },
];

interface PipelineVisualizationProps {
  stage?: number;
  totalStages?: number;
  state: 'idle' | 'running' | 'error' | 'completed';
  runMode: 'build' | 'fetch';
}

export function PipelineVisualization({ stage, state, runMode }: PipelineVisualizationProps) {
  const steps = runMode === 'build'
    ? PIPELINE_STEPS.filter(s => !s.cruiseOnly)
    : PIPELINE_STEPS;

  const progressPct =
    state === 'completed' ? 100
    : state === 'running' && stage !== undefined
      ? Math.round(((stage - 1) / Math.max(steps.length - 1, 1)) * 100)
      : 0;

  return (
    <div className="border border-border-subtle">
      <div className="h-px bg-surface-raised w-full">
        <div className="h-px bg-accent transition-all duration-500" style={{ width: `${progressPct}%` }} />
      </div>

      <div className="hidden sm:flex px-4 py-3 items-center">
        {steps.map((step, i) => {
          const stepNum = i + 1;
          const isDone = state === 'completed' || (state === 'running' && stage !== undefined && stepNum < stage);
          const isActive = state === 'running' && stage === stepNum;
          const isError = state === 'error' && stage === stepNum;

          return (
            <div key={i} className="flex items-center">
              <div className={`flex items-center gap-1.5 py-2 px-3 text-xs transition-all duration-300 ${
                isError ? 'text-danger'
                : isActive ? 'text-white bg-accent/10'
                : isDone ? 'text-text-dim'
                : 'text-text-faint'
              }`}>
                {isError ? (
                  <AlertCircle size={11} className="flex-shrink-0" />
                ) : isActive ? (
                  <Loader2 size={11} className="animate-spin flex-shrink-0 text-accent" />
                ) : isDone ? (
                  <CheckCircle size={11} className="flex-shrink-0 text-success" />
                ) : (
                  <Circle size={11} className="flex-shrink-0" />
                )}
                <span className={`font-medium ${isActive ? 'font-semibold' : ''}`}>
                  <span className="mr-1 opacity-40">{stepNum}</span>
                  {step.label}
                  {step.cruiseOnly && <span className="ml-1 opacity-30 text-[10px]">fetch</span>}
                </span>
              </div>
              {i < steps.length - 1 && (
                <span className="text-text-faint text-xs px-0.5">›</span>
              )}
            </div>
          );
        })}
      </div>

      <div className="flex sm:hidden items-center justify-between px-3 py-3 gap-1">
        {steps.map((step, i) => {
          const stepNum = i + 1;
          const isDone = state === 'completed' || (state === 'running' && stage !== undefined && stepNum < stage);
          const isActive = state === 'running' && stage === stepNum;
          const isError = state === 'error' && stage === stepNum;

          return (
            <div key={i} className="flex items-center gap-1 min-w-0 flex-1">
              <div className={`flex flex-col items-center gap-0.5 min-w-0 flex-1 transition-all duration-300 ${
                isError ? 'text-danger'
                : isActive ? 'text-white'
                : isDone ? 'text-text-dim'
                : 'text-text-faint'
              }`}>
                {isError ? (
                  <AlertCircle size={12} className="flex-shrink-0" />
                ) : isActive ? (
                  <Loader2 size={12} className="animate-spin flex-shrink-0 text-accent" />
                ) : isDone ? (
                  <CheckCircle size={12} className="flex-shrink-0 text-success" />
                ) : (
                  <Circle size={12} className="flex-shrink-0" />
                )}
                <span className="text-[9px] font-medium leading-tight text-center truncate w-full">{step.label}</span>
              </div>
              {i < steps.length - 1 && (
                <span className="text-surface-overlay text-xs flex-shrink-0 mb-3">›</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
