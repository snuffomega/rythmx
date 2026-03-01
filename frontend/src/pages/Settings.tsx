import { useState, useEffect } from 'react';
import { CheckCircle, XCircle, Loader2, RefreshCw, Database, Radio, ChevronDown, ChevronUp } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { settingsApi, libraryApi, imageServiceApi } from '../services/api';
import { ConfirmDialog } from '../components/ConfirmDialog';
import type { LibraryBackend } from '../types';

const BACKEND_LABELS: Record<string, string> = {
  soulsync: 'SoulSync',
  plex: 'Plex Library',
  jellyfin: 'Jellyfin',
  navidrome: 'Navidrome',
};

interface ServiceRowProps {
  name: string;
  subtitle?: string;
  icon: React.ReactNode;
  onTest: () => Promise<{ connected: boolean; message?: string }>;
}

function ServiceCard({ name, subtitle, icon, onTest }: ServiceRowProps) {
  const [status, setStatus] = useState<'idle' | 'testing' | 'connected' | 'error'>('idle');
  const [message, setMessage] = useState<string | null>(null);

  const handleTest = async () => {
    setStatus('testing');
    setMessage(null);
    try {
      const result = await onTest();
      setStatus(result.connected ? 'connected' : 'error');
      setMessage(result.message ?? null);
    } catch (e) {
      setStatus('error');
      setMessage(e instanceof Error ? e.message : 'Connection failed');
    }
  };

  return (
    <div className="bg-[#0e0e0e] border border-[#1a1a1a] p-4 flex flex-col gap-3">
      <div className="flex items-center gap-2.5">
        <div className="w-7 h-7 bg-[#181818] flex items-center justify-center flex-shrink-0">
          {icon}
        </div>
        <div>
          <p className="text-text-primary text-sm font-medium">{name}</p>
          {subtitle && <p className="text-[#444] text-[10px]">{subtitle}</p>}
        </div>
      </div>

      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          {status === 'connected' && (
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 bg-success flex-shrink-0" />
              <span className="text-success text-xs truncate">{message ?? 'Connected'}</span>
            </div>
          )}
          {status === 'error' && (
            <div className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 bg-danger flex-shrink-0" />
              <span className="text-danger text-xs truncate">{message ?? 'Failed'}</span>
            </div>
          )}
          {status === 'idle' && (
            <span className="text-[#444] text-xs">Not tested</span>
          )}
          {status === 'testing' && (
            <span className="text-text-muted text-xs">Testing…</span>
          )}
        </div>

        <button
          onClick={handleTest}
          disabled={status === 'testing'}
          className="btn-ghost flex items-center gap-1.5 text-xs flex-shrink-0"
        >
          {status === 'testing' ? (
            <Loader2 size={12} className="animate-spin" />
          ) : status === 'connected' ? (
            <CheckCircle size={12} className="text-success" />
          ) : status === 'error' ? (
            <XCircle size={12} className="text-danger" />
          ) : (
            <RefreshCw size={12} />
          )}
          Test
        </button>
      </div>
    </div>
  );
}

interface SettingsPageProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function SettingsPage({ toast }: SettingsPageProps) {
  const { data: libraryStatus, loading: libraryLoading, refetch: refetchLibrary } = useApi(() => libraryApi.getStatus());
  const [backend, setBackend] = useState<LibraryBackend>('soulsync');

  useEffect(() => {
    if (libraryStatus?.backend) setBackend(libraryStatus.backend as LibraryBackend);
  }, [libraryStatus?.backend]);
  const [syncing, setSyncing] = useState(false);
  const [switchingBackend, setSwitchingBackend] = useState(false);
  const [warmingCache, setWarmingCache] = useState(false);
  const [confirmClearHistory, setConfirmClearHistory] = useState(false);
  const [confirmClearImageCache, setConfirmClearImageCache] = useState(false);
  const [confirmResetDb, setConfirmResetDb] = useState(false);
  const [dangerOpen, setDangerOpen] = useState(false);

  const handleBackendChange = async (b: LibraryBackend) => {
    setBackend(b);
    setSwitchingBackend(true);
    try {
      await settingsApi.setLibraryBackend(b);
      toast.success(`Switched to ${BACKEND_LABELS[b] ?? b}`);
      refetchLibrary();
    } catch {
      toast.error('Failed to switch backend');
    } finally {
      setSwitchingBackend(false);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      await libraryApi.sync();
      toast.success('Library sync started');
      setTimeout(refetchLibrary, 2000);
    } catch {
      toast.error('Sync failed');
    } finally {
      setSyncing(false);
    }
  };

  const handleClearHistory = async () => {
    try {
      await settingsApi.clearHistory();
      toast.success('History cleared');
    } catch {
      toast.error('Failed to clear history');
    }
    setConfirmClearHistory(false);
  };

  const handleWarmCache = async () => {
    setWarmingCache(true);
    try {
      const result = await imageServiceApi.warmCache(40);
      if (result.submitted > 0) {
        toast.success(`Submitted ${result.submitted} image${result.submitted === 1 ? '' : 's'} for background fetch`);
      } else {
        toast.success('Cache is already warm');
      }
    } catch {
      toast.error('Failed to warm cache');
    } finally {
      setWarmingCache(false);
    }
  };

  const handleClearImageCache = async () => {
    try {
      await settingsApi.clearImageCache();
      toast.success('Image cache cleared');
    } catch {
      toast.error('Failed to clear image cache');
    }
    setConfirmClearImageCache(false);
  };

  const handleResetDb = async () => {
    try {
      await settingsApi.resetDb();
      toast.success('Database reset');
    } catch {
      toast.error('Failed to reset database');
    }
    setConfirmResetDb(false);
  };

  return (
    <div className="py-8 space-y-10">
      <h1 className="page-title">Settings</h1>

      <section>
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-3">Connections</h2>
        <div className="grid grid-cols-2 gap-2">
          <ServiceCard
            name="Last.fm"
            icon={<Radio size={16} className="text-danger" />}
            onTest={settingsApi.testLastfm}
          />
          <ServiceCard
            name="Plex"
            icon={<span className="text-accent font-bold text-sm">P</span>}
            onTest={settingsApi.testPlex}
          />
          <ServiceCard
            key={`library-${backend}`}
            name={BACKEND_LABELS[backend] ?? 'Library DB'}
            icon={<Database size={16} className="text-accent" />}
            onTest={settingsApi.testSoulsync}
          />
          <ServiceCard
            name="Spotify"
            icon={<span className="text-success font-bold text-sm">S</span>}
            onTest={settingsApi.testSpotify}
          />
          <ServiceCard
            name="Fanart.tv"
            subtitle="optional — artist photos"
            icon={<span className="text-[#e88c2a] font-bold text-sm">F</span>}
            onTest={settingsApi.testFanart}
          />
        </div>
      </section>

      <section className="border-t border-[#1a1a1a] pt-8">
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-4">Library</h2>

        <div className="space-y-5">
          <div>
            <label className="label">Library Backend</label>
            <div className="relative mt-1">
              <select
                className="select w-full"
                value={libraryStatus?.backend ?? backend}
                onChange={e => handleBackendChange(e.target.value as LibraryBackend)}
                disabled={switchingBackend}
              >
                <option value="soulsync">SoulSync</option>
                <option value="plex">Plex</option>
                <option value="jellyfin">Jellyfin</option>
                <option value="navidrome">Navidrome</option>
              </select>
              {switchingBackend && (
                <div className="absolute right-8 top-1/2 -translate-y-1/2 pointer-events-none">
                  <Loader2 size={13} className="animate-spin text-text-muted" />
                </div>
              )}
            </div>
          </div>

          {libraryStatus?.track_count !== undefined && (
            <p className="text-[#444] text-xs mt-1">{libraryStatus.track_count.toLocaleString()} tracks indexed</p>
          )}

          <button
            onClick={handleSync}
            disabled={syncing}
            className="btn-secondary flex items-center gap-2 text-sm"
          >
            {syncing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Sync Library Now
          </button>
        </div>
      </section>

      <section className="border-t border-[#1a1a1a] pt-8">
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-4">Images</h2>
        <div className="flex items-center gap-6 flex-wrap">
          <div className="flex items-center gap-3">
            <button
              onClick={handleWarmCache}
              disabled={warmingCache}
              className="btn-secondary flex items-center gap-1.5 text-sm flex-shrink-0"
            >
              {warmingCache ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
              Warm Now
            </button>
            <div>
              <p className="text-text-primary text-sm font-semibold">Warm Image Cache</p>
              <p className="text-[#444] text-xs mt-0.5">Pre-fetch artwork in the background</p>
            </div>
          </div>
          <div className="w-px h-10 bg-[#1a1a1a] flex-shrink-0 hidden sm:block" />
          <div className="flex items-center gap-3">
            <button onClick={() => setConfirmClearImageCache(true)} className="btn-secondary text-sm flex-shrink-0">
              Clear Cache
            </button>
            <div>
              <p className="text-text-primary text-sm font-semibold">Clear Image Cache</p>
              <p className="text-[#444] text-xs mt-0.5">Force all artwork to re-fetch</p>
            </div>
          </div>
        </div>
      </section>

      <section className="border-t border-danger/20 pt-6">
        <button
          onClick={() => setDangerOpen(o => !o)}
          className="flex items-center justify-between w-full text-left"
        >
          <span className="text-danger text-xs font-semibold uppercase tracking-widest">Danger Zone</span>
          {dangerOpen ? <ChevronUp size={14} className="text-danger/60" /> : <ChevronDown size={14} className="text-danger/60" />}
        </button>

        {dangerOpen && (
          <div className="mt-5 border-l-2 border-danger/40 pl-4 space-y-5">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-text-primary text-sm font-semibold">Clear History</p>
                <p className="text-[#444] text-xs mt-0.5">Remove all Cruise Control run history</p>
              </div>
              <button onClick={() => setConfirmClearHistory(true)} className="btn-danger text-sm flex-shrink-0">
                Clear History
              </button>
            </div>
            <div className="flex items-center justify-between gap-4 pt-4 border-t border-danger/10">
              <div>
                <p className="text-text-primary text-sm font-semibold">Reset Database</p>
                <p className="text-[#444] text-xs mt-0.5">Wipe all app data. This cannot be undone.</p>
              </div>
              <button onClick={() => setConfirmResetDb(true)} className="btn-danger text-sm flex-shrink-0">
                Reset DB
              </button>
            </div>
          </div>
        )}
      </section>

      <ConfirmDialog
        open={confirmClearHistory}
        title="Clear History?"
        description="All Cruise Control run history will be permanently deleted. This cannot be undone."
        confirmLabel="Clear History"
        danger
        onConfirm={handleClearHistory}
        onCancel={() => setConfirmClearHistory(false)}
      />

      <ConfirmDialog
        open={confirmClearImageCache}
        title="Clear Image Cache?"
        description="All cached artwork URLs will be removed. Images will re-fetch from external sources on next page load."
        confirmLabel="Clear Cache"
        onConfirm={handleClearImageCache}
        onCancel={() => setConfirmClearImageCache(false)}
      />

      <ConfirmDialog
        open={confirmResetDb}
        title="Reset Database?"
        description="This will permanently delete ALL app data including playlists, queue items, and history. There is no recovery. Are you absolutely sure?"
        confirmLabel="Reset Everything"
        danger
        onConfirm={handleResetDb}
        onCancel={() => setConfirmResetDb(false)}
      />
    </div>
  );
}
