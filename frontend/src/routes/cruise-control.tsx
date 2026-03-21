import { createFileRoute, Outlet, Link } from '@tanstack/react-router';
import { Zap, Sparkles } from 'lucide-react';

export const Route = createFileRoute('/cruise-control')({
  component: CruiseControlLayout,
});

function CruiseControlLayout() {
  return (
    <div className="py-8 space-y-8">
      <div className="space-y-6">
        <div>
          <h1 className="page-title">Cruise Control</h1>
          <p className="text-text-muted text-sm mt-1">Automate and find the music you love</p>
        </div>

        <div className="flex items-center gap-1.5 bg-[#0e0e0e] border border-[#1a1a1a] p-1.5 w-fit">
          <Link
            to="/cruise-control/new-music"
            className="flex items-center gap-2 px-5 py-2.5 text-sm font-semibold transition-all duration-150 text-[#3a3a3a] hover:text-[#666]"
            activeProps={{
              className: 'flex items-center gap-2 px-5 py-2.5 text-sm font-semibold transition-all duration-150 bg-[#1e1e1e] text-text-primary shadow-sm border border-[#2a2a2a]',
            }}
          >
            {({ isActive }) => (
              <>
                <Zap size={14} className={isActive ? 'text-accent' : ''} />
                New Music
              </>
            )}
          </Link>
          <Link
            to="/cruise-control/personal-discovery"
            className="flex items-center gap-2 px-5 py-2.5 text-sm font-semibold transition-all duration-150 text-[#3a3a3a] hover:text-[#666]"
            activeProps={{
              className: 'flex items-center gap-2 px-5 py-2.5 text-sm font-semibold transition-all duration-150 bg-[#1e1e1e] text-text-primary shadow-sm border border-[#2a2a2a]',
            }}
          >
            {({ isActive }) => (
              <>
                <Sparkles size={14} className={isActive ? 'text-accent' : ''} />
                Personal Discovery
              </>
            )}
          </Link>
        </div>
      </div>

      <Outlet />
    </div>
  );
}
