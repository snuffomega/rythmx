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

interface PipelineStatusProps {
  stage?: number;
  totalStages?: number;
  state: 'idle' | 'running' | 'error' | 'completed';
  runMode: 'build' | 'fetch';
}

export function PipelineStatus({ stage, state, runMode }: PipelineStatusProps) {
  const steps = runMode === 'build'
    ? PIPELINE_STEPS.filter(s => !s.cruiseOnly)
    : PIPELINE_STEPS;

  return (
    <div className="border border-[#1a1a1a]">
      {/* Desktop: inline row */}
      <div className="hidden sm:flex px-4 py-3 items-center">
        {steps.map((step, i) => {
          const stepNum = i + 1;
          const isDone = state === 'completed' || (state === 'running' && stage !== undefined && stepNum < stage);
          const isActive = state === 'running' && stage === stepNum;
          const isError = state === 'error' && stage === stepNum;

          return (
            <div key={i} className="flex items-center">
              <div className={`flex items-center gap-1.5 py-2 px-3 text-xs transition-colors ${
                isError ? 'text-danger' : isActive ? 'text-white' : 'text-[#555]'
              }`}>
                {isError ? (
                  <AlertCircle size={11} className="flex-shrink-0" />
                ) : isActive ? (
                  <Loader2 size={11} className="animate-spin flex-shrink-0" />
                ) : isDone ? (
                  <CheckCircle size={11} className="flex-shrink-0" />
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
                <span className="text-[#222] text-xs px-0.5">›</span>
              )}
            </div>
          );
        })}
      </div>

      {/* Mobile: compact step bubbles */}
      <div className="flex sm:hidden items-center justify-between px-3 py-3 gap-1">
        {steps.map((step, i) => {
          const stepNum = i + 1;
          const isDone = state === 'completed' || (state === 'running' && stage !== undefined && stepNum < stage);
          const isActive = state === 'running' && stage === stepNum;
          const isError = state === 'error' && stage === stepNum;

          return (
            <div key={i} className="flex items-center gap-1 min-w-0 flex-1">
              <div className={`flex flex-col items-center gap-0.5 min-w-0 flex-1 ${
                isError ? 'text-danger' : isActive ? 'text-white' : 'text-[#555]'
              }`}>
                {isError ? (
                  <AlertCircle size={12} className="flex-shrink-0" />
                ) : isActive ? (
                  <Loader2 size={12} className="animate-spin flex-shrink-0" />
                ) : (
                  <Circle size={12} className="flex-shrink-0" />
                )}
                <span className="text-[9px] font-medium leading-tight text-center truncate w-full">{step.label}</span>
              </div>
              {i < steps.length - 1 && (
                <span className="text-[#1e1e1e] text-xs flex-shrink-0 mb-3">›</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
