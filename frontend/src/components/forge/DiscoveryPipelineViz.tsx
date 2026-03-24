import { Circle } from 'lucide-react';

const PD_PIPELINE_STEPS = [
  { label: 'Poll History' },
  { label: 'Resolve Artists' },
  { label: 'Find Tracks' },
  { label: 'Check Library' },
  { label: 'Build', cruiseOnly: true },
  { label: 'Queue Tracks', cruiseOnly: true },
  { label: 'Create & Publish' },
];

interface DiscoveryPipelineVizProps {
  runMode: 'build' | 'fetch';
}

export function DiscoveryPipelineViz({ runMode }: DiscoveryPipelineVizProps) {
  const steps = runMode === 'build'
    ? PD_PIPELINE_STEPS.filter(s => !s.cruiseOnly)
    : PD_PIPELINE_STEPS;

  return (
    <div className="border border-[#1a1a1a]">
      <div className="hidden sm:flex px-4 py-3 items-center">
        {steps.map((step, i) => (
          <div key={i} className="flex items-center">
            <div className="flex items-center gap-1.5 py-2 px-3 text-xs text-[#555]">
              <Circle size={11} className="flex-shrink-0" />
              <span className="font-medium">
                <span className="mr-1 opacity-40">{i + 1}</span>
                {step.label}
                {step.cruiseOnly && <span className="ml-1 opacity-30 text-[10px]">fetch</span>}
              </span>
            </div>
            {i < steps.length - 1 && <span className="text-[#222] text-xs px-0.5">›</span>}
          </div>
        ))}
      </div>
      <div className="flex sm:hidden items-center justify-between px-3 py-3 gap-1">
        {steps.map((step, i) => (
          <div key={i} className="flex items-center gap-1 min-w-0 flex-1">
            <div className="flex flex-col items-center gap-0.5 min-w-0 flex-1 text-[#444]">
              <Circle size={12} className="flex-shrink-0" />
              <span className="text-[9px] font-medium leading-tight text-center truncate w-full">{step.label}</span>
            </div>
            {i < steps.length - 1 && <span className="text-[#1e1e1e] text-xs flex-shrink-0 mb-3">›</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
