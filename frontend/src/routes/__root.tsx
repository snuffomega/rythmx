import { useState, useCallback, useEffect, useRef } from 'react';
import { createRootRoute, Outlet, Link, useRouter } from '@tanstack/react-router';
import { Compass, Zap, Activity, BarChart2, Settings, ChevronRight, Menu, Library } from 'lucide-react';
import { ToastContainer } from '../components/ToastContainer';
import { PlayerBar } from '../components/PlayerBar';
import { VinylPlayerScreen } from '../components/VinylPlayerScreen';
import ProcessingSignal from '../components/ProcessingSignal';
import { useToastStore } from '../stores/useToastStore';
import { initApiKey, libraryApi, enrichmentApi } from '../services/api';
import { wsConnect, wsDisconnect } from '../services/wsService';
import { useEnrichmentStore } from '../stores/useEnrichmentStore';
import { usePlayerStore } from '../stores/usePlayerStore';
import { useAudioEngine } from '../hooks/useAudioEngine';
import type { LibraryStatus } from '../types';

const NAV_ITEMS = [
  { to: '/discovery', label: 'Discovery', icon: Compass },
  { to: '/library', label: 'Library', icon: Library },
  { to: '/forge', label: 'The Forge', icon: Zap },
  { to: '/activity', label: 'Activity', icon: Activity },
  { to: '/stats', label: 'Stats', icon: BarChart2 },
  { to: '/settings', label: 'Settings', icon: Settings },
] as const;

export const Route = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
  const [expanded, setExpanded] = useState(false);
  const toasts = useToastStore(s => s.toasts);
  const dismiss = useToastStore(s => s.dismiss);
  const sidebarRef = useRef<HTMLDivElement>(null);
  const router = useRouter();

  const { playerState, isPlaying, currentTrack, hide: hidePlayer,
          expand: expandPlayer, minimize: minimizePlayer, togglePlayPause } = usePlayerStore();

  const { seek, setVolume } = useAudioEngine();

  const globalEnrichRunning = useEnrichmentStore(s => s.running);

  const [globalLibStatus, setGlobalLibStatus] = useState<LibraryStatus | null>(null);
  useEffect(() => {
    initApiKey();
    wsConnect();
    libraryApi.getStatus().then(setGlobalLibStatus).catch(() => {});
    enrichmentApi.status()
      .then(useEnrichmentStore.getState().setFromStatus)
      .catch(() => {});
    return () => wsDisconnect();
  }, []);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (expanded && sidebarRef.current && !sidebarRef.current.contains(e.target as Node)) {
        setExpanded(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [expanded]);

  useEffect(() => {
    // Mini bar should auto-show whenever a track is loaded and user is not in large player.
    // It should hide when playback is fully stopped (no current track).
    if (!currentTrack) {
      if (playerState !== 'hidden') hidePlayer();
      return;
    }
    if (playerState === 'hidden') {
      minimizePlayer();
    }
  }, [currentTrack, playerState, hidePlayer, minimizePlayer]);

  return (
    <div className="min-h-screen bg-base flex">
      {expanded && (
        <div
          className="fixed inset-0 bg-black/50 z-10 md:hidden"
          onClick={() => setExpanded(false)}
        />
      )}

      <aside
        ref={sidebarRef}
        className={`fixed inset-y-0 left-0 z-20 flex flex-col bg-[#0d0d0d] border-r border-border transition-all duration-200 ${
          expanded ? 'w-56' : 'w-16'
        } ${playerState === 'mini' ? 'pb-20' : ''}`}
      >
        <div className="flex items-center h-14 border-b border-border flex-shrink-0 overflow-hidden">
          <button
            onClick={() => setExpanded(v => !v)}
            className="w-16 h-14 flex items-center justify-center flex-shrink-0 text-text-muted hover:text-text-primary transition-colors"
          >
            {expanded ? <ChevronRight size={18} /> : <Menu size={18} />}
          </button>
          {expanded && (
            <span className="text-text-primary font-black text-base tracking-tighter whitespace-nowrap pr-4">
              Rythmx
            </span>
          )}
        </div>

        <div className="h-12 border-b border-border flex-shrink-0 flex items-center overflow-hidden">
          <span className="w-16 flex items-center justify-center flex-shrink-0">
            <ProcessingSignal
              isActive={globalEnrichRunning}
              onClick={() => router.navigate({ to: '/settings' })}
            />
          </span>
          {expanded && (
            <span className="text-text-muted text-xs whitespace-nowrap">
              {globalEnrichRunning ? 'Enriching…' : 'Library Ready'}
            </span>
          )}
        </div>

        <nav className="flex-1 py-2 overflow-y-auto overflow-x-hidden">
          {NAV_ITEMS.map(item => {
            const Icon = item.icon;
            return (
              <Link
                key={item.to}
                to={item.to}
                title={!expanded ? item.label : undefined}
                onClick={() => {
                  setExpanded(false);
                  if (playerState === 'vinyl') {
                    minimizePlayer();
                  }
                }}
                activeOptions={{ includeSearch: false }}
                className="relative w-full flex items-center h-11 transition-colors group text-text-muted hover:text-accent hover:bg-surface-highlight"
                activeProps={{
                  className: 'relative w-full flex items-center h-11 transition-colors group text-text-primary bg-accent/10',
                }}
              >
                {({ isActive }) => (
                  <>
                    {isActive && (
                      <span className="absolute left-0 top-0 bottom-0 w-0.5 bg-accent" />
                    )}
                    <span className="w-16 flex items-center justify-center flex-shrink-0">
                      <Icon size={18} />
                    </span>
                    {expanded && (
                      <span className="text-sm font-medium whitespace-nowrap">{item.label}</span>
                    )}
                  </>
                )}
              </Link>
            );
          })}
        </nav>

      </aside>

      <main className="flex-1 min-w-0 pl-16 flex flex-col min-h-screen">
        {playerState === 'vinyl' ? (
          <VinylPlayerScreen
            isPlaying={isPlaying}
            onPlayPause={togglePlayPause}
            onMinimize={minimizePlayer}
            onSeek={seek}
            onVolumeChange={setVolume}
          />
        ) : (
          <div className={`flex-1 min-h-0 overflow-auto${playerState === 'mini' ? ' pb-20' : ''}`}>
            <div className="max-w-screen-xl mx-auto px-8 xl:px-12 pt-6">
              <Outlet />
            </div>
          </div>
        )}

        {playerState === 'mini' && (
          <PlayerBar
            isPlaying={isPlaying}
            onPlayPause={togglePlayPause}
            onExpand={expandPlayer}
            onSeek={seek}
            onVolumeChange={setVolume}
          />
        )}
      </main>

      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </div>
  );
}
