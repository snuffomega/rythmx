import { useState, useEffect } from 'react';
import { Settings, Loader2, Zap, ChevronDown, ChevronUp } from 'lucide-react';
import { forgeNewMusicApi } from '../services/api';
import { useToastStore } from '../stores/useToastStore';
import { Toggle } from '../components/common';
import type { NewMusicConfig, DiscoveredRelease } from '../types';

// --- Constants (match ForgeCustomDiscovery.tsx pattern) ---
const PERIODS = [
  { value: '7day', label: 'Last 7 days' },
  { value: '1month', label: 'Last 30 days' },
  { value: '3month', label: 'Last 3 months' },
  { value: '6month', label: 'Last 6 months' },
  { value: '12month', label: 'Last 12 months' },
  { value: 'overall', label: 'All time' },
] as const;

const LOOKBACK_OPTIONS = [
  { value: 30, label: '30 days' },
  { value: 60, label: '60 days' },
  { value: 90, label: '90 days' },
  { value: 180, label: '6 months' },
];

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const HOURS = Array.from({ length: 24 }, (_, i) => {
  const h = i % 12 || 12;
  const ampm = i < 12 ? 'AM' : 'PM';
  return { value: i, label: `${h}:00 ${ampm}` };
});

const DEFAULT_CONFIG: NewMusicConfig = {
  nm_min_scrobbles: 10,
  nm_period: '1month',
  nm_lookback_days: 90,
  nm_match_mode: 'loose',
  nm_ignore_keywords: '',
  nm_ignore_artists: '',
  nm_release_kinds: 'album,single,ep',
  nm_schedule_enabled: false,
  nm_schedule_weekday: 1,
  nm_schedule_hour: 8,
};

export function ForgeNewMusic() {
  const toast = {
    success: useToastStore(s => s.success),
    error: useToastStore(s => s.error),
    info: useToastStore(s => s.info),
  };

  const [config, setConfig] = useState<NewMusicConfig>(DEFAULT_CONFIG);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [configOpen, setConfigOpen] = useState(true);
  const [advanced, setAdvanced] = useState(false);
  const [running, setRunning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [results, setResults] = useState<DiscoveredRelease[] | null>(null);
  const [runSummary, setRunSummary] = useState<{ artists_checked: number; releases_found: number } | null>(null);

  const update = <K extends keyof NewMusicConfig>(key: K, value: NewMusicConfig[K]) =>
    setConfig(c => ({ ...c, [key]: value }));

  // Load config on mount; also check for existing results
  useEffect(() => {
    forgeNewMusicApi.getConfig()
      .then(cfg => { setConfig(cfg); setConfigLoaded(true); })
      .catch(() => { setConfigLoaded(true); });

    forgeNewMusicApi.getResults()
      .then(releases => {
        if (releases.length > 0) {
          setResults(releases);
          setConfigOpen(false);
        }
      })
      .catch(() => {});
  }, []);

  const handleSaveConfig = async () => {
    setSaving(true);
    try {
      await forgeNewMusicApi.saveConfig(config);
      toast.success('Settings saved');
    } catch {
      toast.error('Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  const handleRun = async () => {
    setRunning(true);
    try {
      const data = await forgeNewMusicApi.run(config);
      setResults(data.releases);
      setRunSummary({ artists_checked: data.artists_checked, releases_found: data.releases_found });
      setConfigOpen(false);
      toast.success(`Found ${data.releases_found} releases from ${data.artists_checked} artists`);
    } catch {
      toast.error('Failed to run New Music pipeline');
    } finally {
      setRunning(false);
    }
  };

  // --- Config panel ---
  const configPanel = (
    <div className="bg-[#0e0e0e] border border-[#1a1a1a] p-5 space-y-7">

      {/* History Source indicator */}
      <div>
        <p className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-2">Listening History Source</p>
        <div className="flex gap-2">
          <span className="px-2.5 py-1 text-xs bg-[#1a1a1a] border border-[#2a2a2a] text-text-muted">Last.fm</span>
          <span className="text-[#333] text-xs self-center">→ Plex fallback</span>
        </div>
        <p className="text-[#444] text-[11px] mt-1.5">Uses Last.fm scrobbles if configured, otherwise Plex play counts.</p>
      </div>

      {/* Scrobble minimum */}
      <div>
        <label className="block text-text-muted text-xs font-semibold uppercase tracking-widest mb-2">
          Minimum Listens
        </label>
        <input
          type="number"
          min={1}
          max={500}
          value={config.nm_min_scrobbles}
          onChange={e => update('nm_min_scrobbles', parseInt(e.target.value) || 1)}
          className="w-24 bg-[#111] border border-[#2a2a2a] text-text-primary text-sm px-3 py-1.5 focus:outline-none focus:border-accent"
        />
        <p className="text-[#444] text-[11px] mt-1">Minimum plays to qualify an artist as a seed.</p>
      </div>

      {/* Scrobble period */}
      <div>
        <label className="block text-text-muted text-xs font-semibold uppercase tracking-widest mb-2">
          Listening Period
        </label>
        <div className="flex flex-wrap gap-1.5">
          {PERIODS.map(p => (
            <button
              key={p.value}
              onClick={() => update('nm_period', p.value)}
              className={`px-3 py-1.5 text-xs font-semibold border transition-colors ${
                config.nm_period === p.value
                  ? 'bg-[#1e1e1e] text-text-primary border-[#3a3a3a]'
                  : 'text-[#3a3a3a] border-[#1e1e1e] hover:border-[#2a2a2a] hover:text-[#666]'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Release period */}
      <div>
        <label className="block text-text-muted text-xs font-semibold uppercase tracking-widest mb-2">
          Release Window
        </label>
        <div className="flex flex-wrap gap-1.5">
          {LOOKBACK_OPTIONS.map(opt => (
            <button
              key={opt.value}
              onClick={() => update('nm_lookback_days', opt.value)}
              className={`px-3 py-1.5 text-xs font-semibold border transition-colors ${
                config.nm_lookback_days === opt.value
                  ? 'bg-[#1e1e1e] text-text-primary border-[#3a3a3a]'
                  : 'text-[#3a3a3a] border-[#1e1e1e] hover:border-[#2a2a2a] hover:text-[#666]'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <p className="text-[#444] text-[11px] mt-1">How far back to look for new releases.</p>
      </div>

      {/* Release types */}
      <div>
        <label className="block text-text-muted text-xs font-semibold uppercase tracking-widest mb-2">
          Release Types
        </label>
        <div className="flex gap-1.5">
          {[
            { label: 'All', value: 'album,single,ep' },
            { label: 'Album only', value: 'album' },
            { label: 'Single / EP', value: 'single,ep' },
          ].map(opt => (
            <button
              key={opt.value}
              onClick={() => update('nm_release_kinds', opt.value)}
              className={`px-3 py-1.5 text-xs font-semibold border transition-colors ${
                config.nm_release_kinds === opt.value
                  ? 'bg-[#1e1e1e] text-text-primary border-[#3a3a3a]'
                  : 'text-[#3a3a3a] border-[#1e1e1e] hover:border-[#2a2a2a] hover:text-[#666]'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Advanced section */}
      <div>
        <button
          onClick={() => setAdvanced(v => !v)}
          className="flex items-center gap-1.5 text-[#444] hover:text-[#666] text-xs font-semibold uppercase tracking-widest transition-colors"
        >
          {advanced ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          Advanced
        </button>

        {advanced && (
          <div className="mt-4 space-y-5 border-l border-[#1a1a1a] pl-4">
            {/* Match mode */}
            <div>
              <label className="block text-text-muted text-xs font-semibold uppercase tracking-widest mb-2">
                Match Mode
              </label>
              <div className="flex gap-1.5">
                {(['loose', 'strict'] as const).map(mode => (
                  <button
                    key={mode}
                    onClick={() => update('nm_match_mode', mode)}
                    className={`px-3 py-1.5 text-xs font-semibold border capitalize transition-colors ${
                      config.nm_match_mode === mode
                        ? 'bg-[#1e1e1e] text-text-primary border-[#3a3a3a]'
                        : 'text-[#3a3a3a] border-[#1e1e1e] hover:border-[#2a2a2a] hover:text-[#666]'
                    }`}
                  >
                    {mode === 'loose' ? 'Loose (includes features)' : 'Strict (main artist only)'}
                  </button>
                ))}
              </div>
            </div>

            {/* Ignore keywords */}
            <div>
              <label className="block text-text-muted text-xs font-semibold uppercase tracking-widest mb-2">
                Ignore Keywords
              </label>
              <input
                type="text"
                value={config.nm_ignore_keywords}
                onChange={e => update('nm_ignore_keywords', e.target.value)}
                placeholder="live, christmas, remix"
                className="w-full bg-[#111] border border-[#2a2a2a] text-text-primary text-sm px-3 py-1.5 placeholder:text-[#333] focus:outline-none focus:border-accent"
              />
              <p className="text-[#444] text-[11px] mt-1">Comma-separated. Releases containing these words are excluded.</p>
            </div>

            {/* Ignore artists */}
            <div>
              <label className="block text-text-muted text-xs font-semibold uppercase tracking-widest mb-2">
                Ignore Artists
              </label>
              <input
                type="text"
                value={config.nm_ignore_artists}
                onChange={e => update('nm_ignore_artists', e.target.value)}
                placeholder="Artist Name, Another Artist"
                className="w-full bg-[#111] border border-[#2a2a2a] text-text-primary text-sm px-3 py-1.5 placeholder:text-[#333] focus:outline-none focus:border-accent"
              />
              <p className="text-[#444] text-[11px] mt-1">Comma-separated artist names to skip entirely.</p>
            </div>

            {/* Schedule */}
            <div>
              <div className="flex items-center justify-between mb-3">
                <span className="text-text-muted text-xs font-semibold uppercase tracking-widest">Schedule</span>
                <Toggle
                  on={config.nm_schedule_enabled}
                  onChange={v => update('nm_schedule_enabled', v)}
                />
              </div>
              {config.nm_schedule_enabled && (
                <div className="flex gap-3">
                  <select
                    value={config.nm_schedule_weekday}
                    onChange={e => update('nm_schedule_weekday', parseInt(e.target.value))}
                    className="bg-[#111] border border-[#2a2a2a] text-text-primary text-sm px-3 py-1.5 focus:outline-none focus:border-accent"
                  >
                    {WEEKDAYS.map((d, i) => <option key={i} value={i}>{d}</option>)}
                  </select>
                  <select
                    value={config.nm_schedule_hour}
                    onChange={e => update('nm_schedule_hour', parseInt(e.target.value))}
                    className="bg-[#111] border border-[#2a2a2a] text-text-primary text-sm px-3 py-1.5 focus:outline-none focus:border-accent"
                  >
                    {HOURS.map(h => <option key={h.value} value={h.value}>{h.label}</option>)}
                  </select>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-3 pt-2 border-t border-[#1a1a1a]">
        <button
          onClick={handleRun}
          disabled={running || !configLoaded}
          className="flex items-center gap-2 px-5 py-2 text-sm font-semibold bg-accent text-black hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {running ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
          {running ? 'Running\u2026' : 'Run'}
        </button>
        <button
          onClick={handleSaveConfig}
          disabled={saving}
          className="px-4 py-2 text-sm font-semibold bg-[#1e1e1e] border border-[#2a2a2a] text-text-muted hover:text-text-primary transition-colors disabled:opacity-40"
        >
          {saving ? 'Saving\u2026' : 'Save Settings'}
        </button>
      </div>
    </div>
  );

  // --- Release card ---
  const ReleaseCard = ({ release }: { release: DiscoveredRelease }) => (
    <div className={`bg-[#0e0e0e] border border-[#1a1a1a] p-3 flex gap-3 ${release.in_library ? '' : 'opacity-50'}`}>
      {release.cover_url ? (
        <img
          src={release.cover_url}
          alt={release.title}
          className="w-14 h-14 object-cover flex-shrink-0 bg-[#111]"
        />
      ) : (
        <div className="w-14 h-14 bg-[#111] flex-shrink-0" />
      )}
      <div className="min-w-0 flex-1">
        <p className="text-text-primary text-sm font-medium truncate">{release.title}</p>
        <p className="text-text-muted text-xs truncate">{release.artist_name}</p>
        <div className="flex items-center gap-2 mt-1">
          {release.record_type && (
            <span className="text-[10px] text-[#444] uppercase tracking-wide">{release.record_type}</span>
          )}
          {release.release_date && (
            <span className="text-[10px] text-[#333]">{release.release_date.slice(0, 7)}</span>
          )}
          {release.in_library && (
            <span className="text-[10px] text-accent font-semibold">In library</span>
          )}
        </div>
      </div>
    </div>
  );

  return (
    <div className="space-y-6">
      {/* Header row */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">New Music</h2>
          <p className="text-text-muted text-sm mt-1">
            Recent releases from artists in your musical neighborhood.
          </p>
        </div>
        {results !== null && (
          <button
            onClick={() => setConfigOpen(v => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-text-muted border border-[#1a1a1a] hover:border-[#2a2a2a] hover:text-text-primary transition-colors"
          >
            <Settings size={12} />
            Settings
          </button>
        )}
      </div>

      {/* Config panel — visible when configOpen */}
      {configOpen && configPanel}

      {/* Results */}
      {results !== null && (
        <div className="space-y-4">
          {runSummary && (
            <p className="text-[#444] text-xs">
              {runSummary.releases_found} releases found from {runSummary.artists_checked} seed artists
            </p>
          )}
          {results.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3 border border-dashed border-[#1a1a1a]">
              <p className="text-text-muted text-sm">No releases found</p>
              <p className="text-[#444] text-xs text-center max-w-xs">
                Try expanding the release window, lowering the minimum listens, or changing release types.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {results.map(r => <ReleaseCard key={r.id} release={r} />)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
