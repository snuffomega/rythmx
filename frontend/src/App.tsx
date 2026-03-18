import { useState, useCallback, useRef, useEffect } from 'react';
import { Compass, Zap, ListMusic, Activity, BarChart2, Settings, ChevronRight, Menu, Library, Play } from 'lucide-react';
import { Discovery } from './pages/Discovery';
import { Library as LibraryPage } from './pages/Library';
import { CruiseControl } from './pages/CruiseControl';
import { Playlists } from './pages/Playlists';
import { ActivityPage } from './pages/Activity';
import { Stats } from './pages/Stats';
import { SettingsPage } from './pages/Settings';
import { ToastContainer } from './components/ToastContainer';
import { PlayerBar } from './components/PlayerBar';
import { FullPagePlayer } from './components/FullPagePlayer';
import { useToast } from './hooks/useToast';
import { initApiKey, libraryApi, enrichmentApi } from './services/api';
import { useWebSocket } from './hooks/useWebSocket';
import type { LibraryStatus, EnrichmentPipelineStatus, WsEnrichmentProgress } from './types';

type Page = 'discovery' | 'library' | 'cruise-control' | 'playlists' | 'activity' | 'stats' | 'settings';
type PlayerState = 'hidden' | 'mini' | 'fullpage';

const NAV_ITEMS: Array<{ id: Page; label: string; icon: typeof Compass }> = [
  { id: 'discovery', label: 'Discovery', icon: Compass },
  { id: 'library', label: 'Library', icon: Library },
  { id: 'cruise-control', label: 'Cruise Control', icon: Zap },
  { id: 'playlists', label: 'Playlists', icon: ListMusic },
  { id: 'activity', label: 'Activity', icon: Activity },
  { id: 'stats', label: 'Stats', icon: BarChart2 },
  { id: 'settings', label: 'Settings', icon: Settings },
];

export default function App() {
  const [page, setPage] = useState<Page>('discovery');
  const [expanded, setExpanded] = useState(false);
  const [playerState, setPlayerState] = useState<PlayerState>('hidden');
  const [isPlaying, setIsPlaying] = useState(false);
  const { toasts, success, error, dismiss } = useToast();
  const sidebarRef = useRef<HTMLDivElement>(null);

  const toast = { success, error };

  // Seed the API key from the bootstrap endpoint on first load.
  // All subsequent api.ts calls will include X-Api-Key automatically.
  useEffect(() => { initApiKey(); }, []);

  // Global library + enrichment status (drives the status bar on all non-Library pages)
  const [globalLibStatus, setGlobalLibStatus] = useState<LibraryStatus | null>(null);
  const [globalEnrichStatus, setGlobalEnrichStatus] = useState<EnrichmentPipelineStatus | null>(null);

  useEffect(() => {
    libraryApi.getStatus().then(setGlobalLibStatus).catch(() => {});
    enrichmentApi.status().then(setGlobalEnrichStatus).catch(() => {});
  }, []);

  useWebSocket((event, payload) => {
    if (event === 'enrichment_progress') {
      const p = payload as WsEnrichmentProgress;
      setGlobalEnrichStatus(prev => ({
        running: p.running,
        workers: { ...(prev?.workers ?? {}), [p.worker]: { found: p.found, not_found: p.not_found, errors: p.errors, pending: p.pending } },
      }));
    } else if (event === 'enrichment_complete') {
      enrichmentApi.status().then(setGlobalEnrichStatus).catch(() => {});
      libraryApi.getStatus().then(setGlobalLibStatus).catch(() => {});
    } else if (event === 'enrichment_stopped') {
      setGlobalEnrichStatus(prev => prev ? { ...prev, running: false } : prev);
    }
  });

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (expanded && sidebarRef.current && !sidebarRef.current.contains(e.target as Node)) {
        setExpanded(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [expanded]);

  const navigate = useCallback((p: string) => {
    setPage(p as Page);
    setExpanded(false);
  }, []);

  // Called from Library (and future pages) when user clicks play on a track/album.
  const handlePlay = useCallback(() => {
    setPlayerState('mini');
    setIsPlaying(true);
  }, []);

  const handleNowPlayingClick = useCallback(() => {
    setPlayerState(p => p === 'hidden' ? 'mini' : p === 'mini' ? 'hidden' : 'mini');
  }, []);

  const handleNowPlayingDblClick = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setPlayerState('fullpage');
    setIsPlaying(true);
  }, []);

  const renderPage = () => {
    switch (page) {
      case 'discovery': return <Discovery onNavigate={navigate} />;
      case 'library': return <LibraryPage onPlay={handlePlay} />;
      case 'cruise-control': return <CruiseControl toast={toast} />;
      case 'playlists': return <Playlists toast={toast} />;
      case 'activity': return <ActivityPage toast={toast} />;
      case 'stats': return <Stats />;
      case 'settings': return <SettingsPage toast={toast} />;
    }
  };

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
        }`}
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

        <nav className="flex-1 py-2 overflow-y-auto overflow-x-hidden">
          {NAV_ITEMS.map(item => {
            const Icon = item.icon;
            const active = page === item.id;
            return (
              <button
                key={item.id}
                onClick={() => navigate(item.id)}
                title={!expanded ? item.label : undefined}
                className={`relative w-full flex items-center h-11 transition-colors group ${
                  active
                    ? 'text-text-primary bg-accent/10'
                    : 'text-text-muted hover:text-accent hover:bg-surface-highlight'
                }`}
              >
                {active && (
                  <span className="absolute left-0 top-0 bottom-0 w-0.5 bg-accent" />
                )}
                <span className="w-16 flex items-center justify-center flex-shrink-0">
                  <Icon size={18} />
                </span>
                {expanded && (
                  <span className="text-sm font-medium whitespace-nowrap">{item.label}</span>
                )}
              </button>
            );
          })}
        </nav>

        {/* Now Playing sidebar button */}
        <button
          onClick={handleNowPlayingClick}
          onDoubleClick={handleNowPlayingDblClick}
          title={!expanded ? 'Now Playing' : undefined}
          className={`w-full flex items-center h-11 transition-colors border-t border-border ${
            playerState !== 'hidden'
              ? 'text-accent'
              : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          <span className="w-16 flex items-center justify-center flex-shrink-0">
            <Play
              size={16}
              fill={playerState !== 'hidden' && isPlaying ? 'currentColor' : 'none'}
            />
          </span>
          {expanded && (
            <span className="text-sm font-medium whitespace-nowrap">Now Playing</span>
          )}
        </button>

        <div className="h-10 border-t border-border flex-shrink-0 flex items-center overflow-hidden">
          <span className="w-16 flex items-center justify-center flex-shrink-0">
            <Zap size={14} className="text-accent" />
          </span>
          {expanded && (
            <span className="text-text-muted text-xs whitespace-nowrap">Music Discovery Engine</span>
          )}
        </div>
      </aside>

      <main className="flex-1 min-w-0 pl-16 flex flex-col min-h-screen">
        {playerState === 'fullpage' ? (
          <FullPagePlayer
            isPlaying={isPlaying}
            onPlayPause={() => setIsPlaying(p => !p)}
            onMinimize={() => setPlayerState('mini')}
          />
        ) : (
          <div className="flex-1 min-h-0 overflow-auto">
            {/* Global status bar — hidden on Library (has its own richer bar) */}
            {page !== 'library' && globalLibStatus && (
              <div className="px-8 xl:px-12 py-2 bg-[#0a0a0a] border-b border-[#1a1a1a] flex items-center gap-2 text-xs font-mono text-text-muted">
                <Library size={12} className="text-accent flex-shrink-0" />
                <span>{globalLibStatus.track_count?.toLocaleString()} tracks</span>
                <span className="text-[#333]">·</span>
                <span>{(globalLibStatus as unknown as { total_albums?: number }).total_albums?.toLocaleString() ?? '—'} albums</span>
                {globalLibStatus.last_synced && (
                  <>
                    <span className="text-[#333]">·</span>
                    <span className="text-text-muted">synced {new Date(globalLibStatus.last_synced).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</span>
                  </>
                )}
                {globalEnrichStatus && (
                  <span
                    className="ml-1 w-1.5 h-1.5 rounded-full inline-block"
                    title={globalEnrichStatus.running ? 'Enrichment running' : 'Library enriched'}
                    style={{
                      background: '#D4F53C',
                      opacity: globalEnrichStatus.running ? 1 : 0.25,
                      boxShadow: globalEnrichStatus.running ? '0 0 5px #D4F53C88' : 'none',
                      animation: globalEnrichStatus.running ? 'enrichPulse 2s ease-in-out infinite' : 'none',
                    }}
                  />
                )}
              </div>
            )}
            <div className="max-w-screen-xl mx-auto px-8 xl:px-12 pt-6">
              {renderPage()}
            </div>
          </div>
        )}

        {playerState === 'mini' && (
          <PlayerBar
            isPlaying={isPlaying}
            onPlayPause={() => setIsPlaying(p => !p)}
            onExpand={() => setPlayerState('fullpage')}
            onMinimize={() => setPlayerState('hidden')}
          />
        )}
      </main>

      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </div>
  );
}
