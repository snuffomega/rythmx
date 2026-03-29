import { Layers } from 'lucide-react';

export function ForgeBuilder() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-text-primary">Builder</h2>
        <p className="text-text-muted text-sm mt-1">
          Manage and publish your builds. Runs from New Music, Custom Discovery, and Sync appear here.
        </p>
      </div>
      <div className="flex flex-col items-center justify-center py-24 gap-4 border border-dashed border-[#1a1a1a]">
        <Layers size={40} className="text-[#2a2a2a]" />
        <p className="text-text-muted text-sm">No builds yet</p>
        <p className="text-[#444] text-xs text-center max-w-xs">
          Run New Music, Custom Discovery, or Sync to queue a build here
        </p>
      </div>
    </div>
  );
}
