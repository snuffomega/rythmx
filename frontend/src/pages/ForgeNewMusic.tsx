import { Zap } from 'lucide-react';

export function ForgeNewMusic() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-text-primary">New Music</h2>
        <p className="text-text-muted text-sm mt-1">
          Discover recent releases from artists in your neighborhood — similar artists expanded one hop outward from your library.
        </p>
      </div>

      <div className="bg-[#0e0e0e] border border-[#1a1a1a] p-6 space-y-4">
        <div className="flex items-center gap-2">
          <Zap size={15} className="text-accent flex-shrink-0" />
          <span className="text-text-primary text-sm font-medium">How it will work</span>
        </div>
        <ul className="text-[#555] text-sm space-y-2 pl-5 list-disc">
          <li>Builds a similarity graph from your library artists</li>
          <li>Fetches recent releases from discovered neighbors via Deezer</li>
          <li>Shows "In your library" badges for artists you already own</li>
          <li>Sends results to Builder for review and publish</li>
        </ul>
        <div className="pt-2 border-t border-[#1a1a1a]">
          <button
            disabled
            className="px-4 py-2 text-sm font-semibold bg-[#111] text-[#333] border border-[#1e1e1e] cursor-not-allowed"
          >
            Run
          </button>
          <p className="text-[#444] text-xs mt-2">
            New Music pipeline is being rebuilt in Phase 27b
          </p>
        </div>
      </div>
    </div>
  );
}
