import { useState, useEffect } from 'react';
import { Loader2, Radio, Database } from 'lucide-react';
import { settingsApi } from '../../services/api';
import type { LibraryPlatform, Settings } from '../../types';

export const PLATFORM_LABELS: Record<string, string> = {
  plex: 'Plex',
  jellyfin: 'Jellyfin',
  navidrome: 'Navidrome',
};

interface ServiceRowProps {
  name: string;
  subtitle?: string;
  icon: React.ReactNode;
  configured?: boolean;
  onTest: () => Promise<{ connected: boolean; message?: string }>;
  onResult: (result: { connected: boolean; message?: string }) => void;
}

function ServiceCard({ name, subtitle, icon, configured, onTest, onResult }: ServiceRowProps) {
  const [status, setStatus] = useState<'idle' | 'testing' | 'connected'>('idle');

  const handleTest = async () => {
    setStatus('testing');
    try {
      const result = await onTest();
      setStatus(result.connected ? 'connected' : 'idle');
      onResult(result);
    } catch {
      setStatus('idle');
      onResult({ connected: false, message: 'Connection test failed' });
    }
  };

  return (
    <button
      type="button"
      onClick={() => void handleTest()}
      disabled={status === 'testing'}
      className="bg-[#0e0e0e] border border-[#1a1a1a] p-4 flex items-center gap-3 min-h-[68px] text-left transition-colors hover:border-[#303030] disabled:opacity-70 disabled:cursor-not-allowed"
    >
      <div className="w-7 h-7 bg-[#181818] flex items-center justify-center flex-shrink-0">
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-text-primary text-sm font-medium">{name}</p>
        {subtitle && <p className="text-[#444] text-[10px] mt-0.5">{subtitle}</p>}
        {configured !== undefined && (
          <p className="text-[#4e4e4e] text-[10px] mt-0.5">
            {configured ? 'configured' : 'not configured'}
          </p>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {status === 'testing' && <Loader2 size={12} className="animate-spin text-text-muted" />}
        <span
          className={`w-2.5 h-2.5 rounded-full border ${
            status === 'connected'
              ? 'bg-accent border-accent'
              : 'bg-transparent border-[#4a4a4a]'
          }`}
          title={status === 'connected' ? 'Connected' : 'Not tested / unavailable'}
        />
      </div>
    </button>
  );
}

function ActivePlatformCard({ platform, configured }: { platform: LibraryPlatform; configured: boolean }) {
  return (
    <div className="bg-[#0e0e0e] border border-accent/60 p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 bg-[#181818] flex items-center justify-center flex-shrink-0">
            <span className="text-accent font-bold text-sm">{PLATFORM_LABELS[platform][0]}</span>
          </div>
          <div className="min-w-0">
            <p className="text-text-primary text-sm font-medium">{PLATFORM_LABELS[platform]}</p>
            <p className="text-[#4e4e4e] text-[10px] mt-0.5">
              {configured ? 'configured' : 'not configured'}
            </p>
          </div>
        </div>
        <span className="text-[10px] font-mono uppercase tracking-wider text-accent">Active</span>
      </div>
    </div>
  );
}

export interface ConnectionsSectionProps {
  platform: LibraryPlatform;
  settingsStatus: Settings | null;
  onServiceTestResult: (label: string, result: { connected: boolean; message?: string }) => void;
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function ConnectionsSection({
  platform,
  settingsStatus,
  onServiceTestResult,
  toast,
}: ConnectionsSectionProps) {
  const platformConfigured = (p: LibraryPlatform): boolean => {
    if (p === 'navidrome') return Boolean(settingsStatus?.navidrome_configured);
    if (p === 'plex') return Boolean(settingsStatus?.plex_configured);
    return false;
  };

  const testActiveLibraryPlatform = () => {
    if (platform === 'navidrome') return settingsApi.testNavidrome();
    if (platform === 'plex') return settingsApi.testPlex();
    return Promise.resolve({ connected: false, message: 'Jellyfin not yet implemented' });
  };

  const showSoulSyncCard = Boolean(settingsStatus?.soulsync_url || settingsStatus?.soulsync_db_accessible);

  return (
    <section>
      <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-3">Connections</h2>
      <h3 className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-2">Library Platform</h3>
      <div className="mb-5">
        <ActivePlatformCard platform={platform} configured={platformConfigured(platform)} />
      </div>

      <h3 className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-2">Services</h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {(settingsStatus === null || settingsStatus?.lastfm_configured) && (
          <ServiceCard
            name="Last.fm"
            subtitle="Click to test"
            icon={<Radio size={16} className="text-danger" />}
            configured={settingsStatus?.lastfm_configured}
            onTest={settingsApi.testLastfm}
            onResult={(result) => onServiceTestResult('Last.fm', result)}
          />
        )}
        {(settingsStatus === null || platformConfigured(platform)) && (
          <ServiceCard
            key={`library-${platform}`}
            name={PLATFORM_LABELS[platform] ?? platform}
            subtitle="Click to test"
            icon={<span className="text-accent font-bold text-sm">{(PLATFORM_LABELS[platform] ?? platform)[0]}</span>}
            configured={platformConfigured(platform)}
            onTest={testActiveLibraryPlatform}
            onResult={(result) => onServiceTestResult(PLATFORM_LABELS[platform] ?? platform, result)}
          />
        )}
        {showSoulSyncCard && (
          <ServiceCard
            name="SoulSync"
            subtitle="Click to test"
            icon={<Database size={16} className="text-accent" />}
            configured={settingsStatus?.soulsync_db_accessible}
            onTest={settingsApi.testSoulsync}
            onResult={(result) => onServiceTestResult('SoulSync', result)}
          />
        )}
        {(settingsStatus === null || settingsStatus?.spotify_configured) && (
          <ServiceCard
            name="Spotify"
            subtitle="Click to test"
            icon={<span className="text-success font-bold text-sm">S</span>}
            configured={settingsStatus?.spotify_configured}
            onTest={settingsApi.testSpotify}
            onResult={(result) => onServiceTestResult('Spotify', result)}
          />
        )}
        {(settingsStatus === null || settingsStatus?.fanart_configured) && (
          <ServiceCard
            name="Fanart.tv"
            subtitle="Optional • click to test"
            icon={<span className="text-[#e88c2a] font-bold text-sm">F</span>}
            configured={settingsStatus?.fanart_configured}
            onTest={settingsApi.testFanart}
            onResult={(result) => onServiceTestResult('Fanart.tv', result)}
          />
        )}
      </div>
    </section>
  );
}
