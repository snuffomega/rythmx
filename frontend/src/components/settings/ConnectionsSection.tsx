import { useMemo, useState } from 'react';
import { Loader2, Radio, Database } from 'lucide-react';
import { settingsApi } from '../../services/api';
import type { LibraryPlatform, Settings, ConnectionStatus } from '../../types';

export const PLATFORM_LABELS: Record<string, string> = {
  plex: 'Plex',
  jellyfin: 'Jellyfin',
  navidrome: 'Navidrome',
};

type ServiceState = 'idle' | 'testing' | 'connected' | 'failed';

interface ServiceCardProps {
  name: string;
  subtitle?: string;
  icon: React.ReactNode;
  configured?: boolean;
  status: ServiceState;
  onTest: () => void;
  disabled?: boolean;
  badge?: string;
}

interface ServiceDescriptor {
  key: string;
  label: string;
  subtitle?: string;
  icon: React.ReactNode;
  configured?: boolean;
  run: () => Promise<ConnectionStatus>;
}

function ServiceCard({
  name,
  subtitle,
  icon,
  configured,
  status,
  onTest,
  disabled = false,
  badge,
}: ServiceCardProps) {
  return (
    <button
      type="button"
      onClick={onTest}
      disabled={disabled || status === 'testing'}
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
        {badge && (
          <span className="text-[10px] font-mono uppercase tracking-wider text-accent">{badge}</span>
        )}
        {status === 'testing' && <Loader2 size={12} className="animate-spin text-text-muted" />}
        <span
          className={`w-2.5 h-2.5 rounded-full border ${
            status === 'connected'
              ? 'bg-accent border-accent'
              : status === 'failed'
                ? 'bg-danger border-danger'
                : 'bg-transparent border-[#4a4a4a]'
          }`}
          title={
            status === 'connected'
              ? 'Connected'
              : status === 'failed'
                ? 'Failed'
                : 'Not tested'
          }
        />
      </div>
    </button>
  );
}

function ServiceSkeleton() {
  return (
    <div className="bg-[#0e0e0e] border border-[#1a1a1a] p-4 min-h-[68px] animate-pulse">
      <div className="h-3 w-24 bg-[#1f1f1f] rounded mb-2" />
      <div className="h-2 w-20 bg-[#1a1a1a] rounded" />
    </div>
  );
}

export interface ConnectionsSectionProps {
  platform: LibraryPlatform;
  settingsStatus: Settings | null;
  settingsLoaded: boolean;
  onServiceTestResult: (label: string, result: { connected: boolean; message?: string }) => void;
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function ConnectionsSection({
  platform,
  settingsStatus,
  settingsLoaded,
  onServiceTestResult,
  toast,
}: ConnectionsSectionProps) {
  const [verifyAllLoading, setVerifyAllLoading] = useState(false);
  const [serviceState, setServiceState] = useState<Record<string, ServiceState>>({});

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

  const showSoulSyncCard = Boolean(settingsStatus?.soulsync_url || settingsStatus?.soulsync_db);

  const services = useMemo<ServiceDescriptor[]>(
    () => [
      {
        key: 'library_platform',
        label: `Library Platform (${PLATFORM_LABELS[platform] ?? platform})`,
        subtitle: 'Active platform • click to test',
        icon: (
          <span className="text-accent font-bold text-sm">
            {(PLATFORM_LABELS[platform] ?? platform)[0]}
          </span>
        ),
        configured: platformConfigured(platform),
        run: testActiveLibraryPlatform,
      },
      ...(settingsStatus?.lastfm_configured
        ? [{
            key: 'lastfm',
            label: 'Last.fm',
            subtitle: 'Click to test',
            icon: <Radio size={16} className="text-danger" />,
            configured: settingsStatus.lastfm_configured,
            run: settingsApi.testLastfm,
          } satisfies ServiceDescriptor]
        : []),
      ...(showSoulSyncCard
        ? [{
            key: 'soulsync',
            label: 'SoulSync',
            subtitle: 'Click to test',
            icon: <Database size={16} className="text-accent" />,
            configured: settingsStatus?.soulsync_db_accessible,
            run: settingsApi.testSoulsync,
          } satisfies ServiceDescriptor]
        : []),
      ...(settingsStatus?.spotify_configured
        ? [{
            key: 'spotify',
            label: 'Spotify',
            subtitle: 'Click to test',
            icon: <span className="text-success font-bold text-sm">S</span>,
            configured: settingsStatus.spotify_configured,
            run: settingsApi.testSpotify,
          } satisfies ServiceDescriptor]
        : []),
      ...(settingsStatus?.fanart_configured
        ? [{
            key: 'fanart',
            label: 'Fanart.tv',
            subtitle: 'Optional • click to test',
            icon: <span className="text-[#e88c2a] font-bold text-sm">F</span>,
            configured: settingsStatus.fanart_configured,
            run: settingsApi.testFanart,
          } satisfies ServiceDescriptor]
        : []),
    ],
    [platform, settingsStatus, showSoulSyncCard],
  );

  const runServiceTest = async (
    service: ServiceDescriptor,
    emitPerServiceToast: boolean,
  ): Promise<ConnectionStatus> => {
    setServiceState((prev) => ({ ...prev, [service.key]: 'testing' }));
    try {
      const result = await service.run();
      setServiceState((prev) => ({
        ...prev,
        [service.key]: result.connected ? 'connected' : 'failed',
      }));
      if (emitPerServiceToast) {
        onServiceTestResult(service.label, result);
      }
      return result;
    } catch {
      const result = { connected: false, message: 'Connection test failed' };
      setServiceState((prev) => ({ ...prev, [service.key]: 'failed' }));
      if (emitPerServiceToast) {
        onServiceTestResult(service.label, result);
      }
      return result;
    }
  };

  const handleVerifyAll = async () => {
    if (!services.length) return;
    setVerifyAllLoading(true);
    try {
      const results = await Promise.all(
        services.map(async (service) => ({ service, result: await runServiceTest(service, false) })),
      );
      const okCount = results.filter(({ result }) => result.connected).length;
      const failCount = results.length - okCount;
      if (failCount === 0) {
        toast.success(`Verify All complete: ${okCount}/${results.length} connected`);
        return;
      }
      const failedLabels = results
        .filter(({ result }) => !result.connected)
        .map(({ service }) => service.label)
        .join(', ');
      toast.error(`Verify All: ${okCount}/${results.length} connected. Failed: ${failedLabels}`);
    } finally {
      setVerifyAllLoading(false);
    }
  };

  return (
    <section>
      <div className="flex items-center justify-between gap-3 mb-3">
        <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest">Connections</h2>
        <button
          type="button"
          onClick={() => void handleVerifyAll()}
          disabled={!settingsLoaded || verifyAllLoading || !services.length}
          className="px-3 py-1.5 text-[11px] font-semibold tracking-wide uppercase bg-[#141414] border border-[#303030] text-text-primary hover:border-accent disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {verifyAllLoading ? 'Verifying...' : 'Verify All'}
        </button>
      </div>

      <h3 className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-2">Services</h3>
      {!settingsLoaded ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          <ServiceSkeleton />
          <ServiceSkeleton />
          <ServiceSkeleton />
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {services.map((service, index) => (
            <ServiceCard
              key={service.key}
              name={service.label}
              subtitle={service.subtitle}
              icon={service.icon}
              configured={service.configured}
              status={serviceState[service.key] ?? 'idle'}
              onTest={() => { void runServiceTest(service, true); }}
              disabled={verifyAllLoading}
              badge={index === 0 ? 'Active' : undefined}
            />
          ))}
        </div>
      )}
    </section>
  );
}
