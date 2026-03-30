import { createFileRoute, Outlet, Link } from '@tanstack/react-router';
import { useEffect } from 'react';
import { ListMusic, Zap, Sparkles, Layers } from 'lucide-react';
import { settingsApi } from '../services/api';
import { useSettingsStore } from '../stores/useSettingsStore';

export const Route = createFileRoute('/forge')({
  component: ForgeLayout,
});

const TAB_BASE = 'flex items-center gap-2 px-5 py-2.5 text-sm font-semibold transition-all duration-150 text-[#3a3a3a] hover:text-[#666]';
const TAB_ACTIVE = 'flex items-center gap-2 px-5 py-2.5 text-sm font-semibold transition-all duration-150 bg-accent text-black';

function ForgeLayout() {
  const { initialized, initFromApi } = useSettingsStore();

  useEffect(() => {
    if (!initialized) {
      settingsApi.get().then(initFromApi).catch(() => {});
    }
  }, [initialized, initFromApi]);

  return (
    <div className="py-8 space-y-8">
      <div className="space-y-6">
        <div>
          <h1 className="page-title">The Forge</h1>
          <p className="text-text-muted text-sm mt-1">Build, discover, and manage your music pipeline</p>
        </div>

        <div className="flex items-center gap-1.5 bg-[#0e0e0e] border border-[#1a1a1a] p-1.5 w-fit">
          <Link to="/forge/builder" className={TAB_BASE} activeProps={{ className: TAB_ACTIVE }}>
            {({ isActive }) => (
              <>
                <Layers size={14} className={isActive ? 'text-black' : ''} />
                Builder
              </>
            )}
          </Link>
          <Link to="/forge/new-music" className={TAB_BASE} activeProps={{ className: TAB_ACTIVE }}>
            {({ isActive }) => (
              <>
                <Zap size={14} className={isActive ? 'text-black' : ''} />
                New Music
              </>
            )}
          </Link>
          <Link to="/forge/custom-discovery" className={TAB_BASE} activeProps={{ className: TAB_ACTIVE }}>
            {({ isActive }) => (
              <>
                <Sparkles size={14} className={isActive ? 'text-black' : ''} />
                Custom Discovery
              </>
            )}
          </Link>
          <Link to="/forge/sync" className={TAB_BASE} activeProps={{ className: TAB_ACTIVE }}>
            {({ isActive }) => (
              <>
                <ListMusic size={14} className={isActive ? 'text-black' : ''} />
                Sync
              </>
            )}
          </Link>
        </div>
      </div>

      <Outlet />
    </div>
  );
}
