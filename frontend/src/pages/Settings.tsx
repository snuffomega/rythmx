import { useState, useEffect } from 'react';
import { CheckCircle, XCircle, Loader2, RefreshCw, Database, Radio, ChevronDown, ChevronUp, Key, Eye, EyeOff, Copy } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { settingsApi, libraryApi, imageServiceApi, setApiKey } from '../services/api';
import { ConfirmDialog } from '../components/ConfirmDialog';
import type { LibraryPlatform, LibraryEnrichStatus, SpotifyEnrichStatus, LastfmTagsStatus, DeezerBpmStatus } from '../types';

const PLATFORM_LABELS: Record<string, string> = {
  plex: 'Plex',
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
  const { data: spotifyStatusData, refetch: refetchSpotifyStatus } = useApi(() => libraryApi.spotifyStatus());
  const { data: lastfmTagsData, refetch: refetchLastfmTags } = useApi(() => libraryApi.lastfmTagsStatus());
  const { data: deezerBpmData, refetch: refetchDeezerBpm } = useApi(() => libraryApi.deezerBpmStatus());
  const [platform, setPlatform] = useState<LibraryPlatform>('plex');

  useEffect(() => {
    if (libraryStatus?.platform) setPlatform(libraryStatus.platform as LibraryPlatform);
  }, [libraryStatus?.platform]);
  const [syncing, setSyncing] = useState(false);
  const [enriching, setEnriching] = useState(false);
  const [liveEnrichStatus, setLiveEnrichStatus] = useState<LibraryEnrichStatus | null>(null);
  const [enrichingSpotify, setEnrichingSpotify] = useState(false);
  const [liveSpotifyStatus, setLiveSpotifyStatus] = useState<SpotifyEnrichStatus | null>(null);
  const [enrichingLastfmTags, setEnrichingLastfmTags] = useState(false);
  const [liveLastfmTagsStatus, setLiveLastfmTagsStatus] = useState<LastfmTagsStatus | null>(null);
  const [enrichingDeezerBpm, setEnrichingDeezerBpm] = useState(false);
  const [liveDeezerBpmStatus, setLiveDeezerBpmStatus] = useState<DeezerBpmStatus | null>(null);
  const [switchingBackend, setSwitchingBackend] = useState(false);
  const [warmingCache, setWarmingCache] = useState(false);
  const [confirmClearHistory, setConfirmClearHistory] = useState(false);
  const [confirmClearImageCache, setConfirmClearImageCache] = useState(false);
  const [confirmResetDb, setConfirmResetDb] = useState(false);
  const [dangerOpen, setDangerOpen] = useState(false);
  const [apiKey, setApiKeyState] = useState<string | null>(null);
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    settingsApi.getApiKey().then(setApiKeyState).catch(() => {});
  }, []);

  const handlePlatformChange = async (p: LibraryPlatform) => {
    setPlatform(p);
    setSwitchingBackend(true);
    try {
      await settingsApi.setLibraryPlatform(p);
      toast.success(`Switched to ${PLATFORM_LABELS[p] ?? p}`);
      refetchLibrary();
    } catch {
      toast.error('Failed to switch platform');
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

  const handleEnrich = async () => {
    setEnriching(true);
    try {
      await libraryApi.enrich();
    } catch {
      toast.error('Failed to start enrich');
      setEnriching(false);
    }
  };

  useEffect(() => {
    if (!enriching) return;
    const id = setInterval(async () => {
      try {
        const s = await libraryApi.enrichStatus();
        setLiveEnrichStatus(s);
        if (!s.enrich_running) {
          setEnriching(false);
          refetchLibrary();
          clearInterval(id);
        }
      } catch {
        setEnriching(false);
        clearInterval(id);
      }
    }, 3000);
    return () => clearInterval(id);
  }, [enriching]);

  const handleEnrichSpotify = async () => {
    setEnrichingSpotify(true);
    try {
      await libraryApi.enrichSpotify();
    } catch {
      toast.error('Failed to start Spotify enrich');
      setEnrichingSpotify(false);
    }
  };

  useEffect(() => {
    if (!enrichingSpotify) return;
    const id = setInterval(async () => {
      try {
        const s = await libraryApi.spotifyStatus();
        setLiveSpotifyStatus(s);
        if (!s.enrich_running) {
          setEnrichingSpotify(false);
          refetchSpotifyStatus();
          clearInterval(id);
        }
      } catch {
        setEnrichingSpotify(false);
        clearInterval(id);
      }
    }, 3000);
    return () => clearInterval(id);
  }, [enrichingSpotify]);

  const handleEnrichLastfmTags = async () => {
    setEnrichingLastfmTags(true);
    try {
      await libraryApi.enrichLastfmTags();
    } catch {
      toast.error('Failed to start genre enrich');
      setEnrichingLastfmTags(false);
    }
  };

  useEffect(() => {
    if (!enrichingLastfmTags) return;
    const id = setInterval(async () => {
      try {
        const s = await libraryApi.lastfmTagsStatus();
        setLiveLastfmTagsStatus(s);
        if (!s.enrich_running) {
          setEnrichingLastfmTags(false);
          refetchLastfmTags();
          clearInterval(id);
        }
      } catch {
        setEnrichingLastfmTags(false);
        clearInterval(id);
      }
    }, 3000);
    return () => clearInterval(id);
  }, [enrichingLastfmTags]);

  const handleEnrichDeezerBpm = async () => {
    setEnrichingDeezerBpm(true);
    try {
      await libraryApi.enrichDeezerBpm();
    } catch {
      toast.error('Failed to start BPM enrich');
      setEnrichingDeezerBpm(false);
    }
  };

  useEffect(() => {
    if (!enrichingDeezerBpm) return;
    const id = setInterval(async () => {
      try {
        const s = await libraryApi.deezerBpmStatus();
        setLiveDeezerBpmStatus(s);
        if (!s.enrich_running) {
          setEnrichingDeezerBpm(false);
          refetchDeezerBpm();
          clearInterval(id);
        }
      } catch {
        setEnrichingDeezerBpm(false);
        clearInterval(id);
      }
    }, 3000);
    return () => clearInterval(id);
  }, [enrichingDeezerBpm]);

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

  const handleCopyApiKey = () => {
    if (!apiKey) return;
    navigator.clipboard.writeText(apiKey).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleRegenerateApiKey = async () => {
    setRegenerating(true);
    try {
      const newKey = await settingsApi.regenerateApiKey();
      setApiKeyState(newKey);
      setApiKey(newKey); // update the in-memory + localStorage key for subsequent requests
      toast.success('API key regenerated');
    } catch {
      toast.error('Failed to regenerate API key');
    } finally {
      setRegenerating(false);
    }
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
            key={`library-${platform}`}
            name="SoulSync Enrichment"
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
            <label className="label">Library Platform</label>
            <div className="relative mt-1">
              <select
                className="select w-full"
                value={libraryStatus?.platform ?? platform}
                onChange={e => handlePlatformChange(e.target.value as LibraryPlatform)}
                disabled={switchingBackend}
              >
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

          {libraryStatus?.last_synced && (
            <p className="text-[#444] text-xs">Last synced: {libraryStatus.last_synced}</p>
          )}

          <button
            onClick={handleSync}
            disabled={syncing}
            className="btn-secondary flex items-center gap-2 text-sm"
          >
            {syncing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Sync Library Now
          </button>

          {platform === 'plex' && (
            <div className="space-y-2 pt-1">
              {(() => {
                const status = liveEnrichStatus ?? libraryStatus;
                const total = status?.total_albums ?? 0;
                const enriched = status?.enriched_albums ?? 0;
                const pct = status?.enrich_pct ?? 0;
                return total > 0 ? (
                  <div className="space-y-1">
                    <p className="text-[#444] text-xs">
                      {enriched.toLocaleString()} / {total.toLocaleString()} albums enriched ({pct}%)
                    </p>
                    <div className="h-0.5 bg-[#1a1a1a] w-full">
                      <div
                        className="h-0.5 bg-accent transition-all duration-500"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                ) : null;
              })()}
              <button
                onClick={handleEnrich}
                disabled={enriching}
                className="btn-secondary flex items-center gap-2 text-sm"
              >
                {enriching ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                Enrich Library
              </button>
            </div>
          )}

          {spotifyStatusData?.spotify_available && (
            <div className="space-y-2 pt-1">
              {(() => {
                const s = liveSpotifyStatus ?? spotifyStatusData;
                const enriched = s?.enriched_artists ?? 0;
                const total = s?.total_artists ?? 0;
                const pct = total > 0 ? Math.round((enriched / total) * 100) : 0;
                const lastRun = s?.last_run;
                return (
                  <div className="space-y-1">
                    {total > 0 && (
                      <>
                        <p className="text-[#444] text-xs">
                          {enriched.toLocaleString()} / {total.toLocaleString()} artists Spotify-enriched ({pct}%)
                          {lastRun && <span className="ml-2">· last run {lastRun}</span>}
                        </p>
                        <div className="h-0.5 bg-[#1a1a1a] w-full">
                          <div
                            className="h-0.5 bg-success transition-all duration-500"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </>
                    )}
                  </div>
                );
              })()}
              <button
                onClick={handleEnrichSpotify}
                disabled={enrichingSpotify}
                className="btn-secondary flex items-center gap-2 text-sm"
              >
                {enrichingSpotify ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                Enrich Spotify
              </button>
            </div>
          )}

          {lastfmTagsData?.lastfm_available && (
            <div className="space-y-2 pt-1">
              {(() => {
                const s = liveLastfmTagsStatus ?? lastfmTagsData;
                const ea = s?.enriched_artists ?? 0;
                const ta = s?.total_artists ?? 0;
                const eab = s?.enriched_albums ?? 0;
                const tab = s?.total_albums ?? 0;
                const pct = ta > 0 ? Math.round((ea / ta) * 100) : 0;
                const lastRun = s?.last_run;
                return (
                  <div className="space-y-1">
                    {ta > 0 && (
                      <>
                        <p className="text-[#444] text-xs">
                          {ea.toLocaleString()} / {ta.toLocaleString()} artists · {eab.toLocaleString()} / {tab.toLocaleString()} albums tagged ({pct}%)
                          {lastRun && <span className="ml-2">· last run {lastRun}</span>}
                        </p>
                        <div className="h-0.5 bg-[#1a1a1a] w-full">
                          <div
                            className="h-0.5 bg-accent transition-all duration-500"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </>
                    )}
                  </div>
                );
              })()}
              <button
                onClick={handleEnrichLastfmTags}
                disabled={enrichingLastfmTags}
                className="btn-secondary flex items-center gap-2 text-sm"
              >
                {enrichingLastfmTags ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                Enrich Genres
              </button>
            </div>
          )}

          {(deezerBpmData?.total_albums ?? 0) > 0 && (
            <div className="space-y-2 pt-1">
              {(() => {
                const s = liveDeezerBpmStatus ?? deezerBpmData;
                const ea = s?.enriched_albums ?? 0;
                const ta = s?.total_albums ?? 0;
                const et = s?.enriched_tracks ?? 0;
                const pct = ta > 0 ? Math.round((ea / ta) * 100) : 0;
                const lastRun = s?.last_run;
                return (
                  <div className="space-y-1">
                    {ta > 0 && (
                      <>
                        <p className="text-[#444] text-xs">
                          {ea.toLocaleString()} / {ta.toLocaleString()} albums · {et.toLocaleString()} tracks with BPM ({pct}%)
                          {lastRun && <span className="ml-2">· last run {lastRun}</span>}
                        </p>
                        <div className="h-0.5 bg-[#1a1a1a] w-full">
                          <div
                            className="h-0.5 bg-accent transition-all duration-500"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </>
                    )}
                  </div>
                );
              })()}
              <button
                onClick={handleEnrichDeezerBpm}
                disabled={enrichingDeezerBpm}
                className="btn-secondary flex items-center gap-2 text-sm"
              >
                {enrichingDeezerBpm ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                Enrich BPM
              </button>
            </div>
          )}
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

      <section className="border-t border-[#1a1a1a] pt-8">
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-4">Security</h2>
        <div className="bg-[#0e0e0e] border border-[#1a1a1a] p-4 space-y-3">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 bg-[#181818] flex items-center justify-center flex-shrink-0">
              <Key size={14} className="text-text-muted" />
            </div>
            <div>
              <p className="text-text-primary text-sm font-medium">API Key</p>
              <p className="text-[#444] text-[10px]">Include as X-Api-Key header for external integrations</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div className="flex-1 min-w-0 bg-[#141414] border border-[#222] px-3 py-2 font-mono text-xs text-text-muted truncate">
              {apiKey
                ? (apiKeyVisible ? apiKey : '•'.repeat(24))
                : <span className="text-[#333]">Loading…</span>}
            </div>
            <button
              onClick={() => setApiKeyVisible(v => !v)}
              className="btn-secondary p-2 flex-shrink-0"
              title={apiKeyVisible ? 'Hide key' : 'Show key'}
            >
              {apiKeyVisible ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
            <button
              onClick={handleCopyApiKey}
              disabled={!apiKey}
              className="btn-secondary p-2 flex-shrink-0"
              title="Copy to clipboard"
            >
              {copied ? <CheckCircle size={14} className="text-success" /> : <Copy size={14} />}
            </button>
          </div>

          <div className="flex justify-end">
            <button
              onClick={handleRegenerateApiKey}
              disabled={regenerating}
              className="btn-secondary text-xs flex items-center gap-1.5"
            >
              {regenerating ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              Regenerate
            </button>
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
