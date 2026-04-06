import { useState, useEffect } from 'react';
import { useApi } from '../hooks/useApi';
import { libraryApi, libraryBrowseApi, settingsApi } from '../services/api';
import { useSettingsStore } from '../stores/useSettingsStore';
import { ConnectionsSection } from '../components/settings/ConnectionsSection';
import { CapabilitiesSection } from '../components/settings/CapabilitiesSection';
import { EnrichmentSection } from '../components/settings/EnrichmentSection';
import { AuditReviewModal } from '../components/settings/AuditReviewModal';
import { SecuritySection } from '../components/settings/SecuritySection';
import { DangerZoneSection } from '../components/settings/DangerZoneSection';
import type { LibraryPlatform, Settings } from '../types';

// ---------------------------------------------------------------------------
// SettingsPage — orchestrator only; all sections own their own logic
// ---------------------------------------------------------------------------

interface SettingsPageProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function SettingsPage({ toast }: SettingsPageProps) {
  const { data: libraryStatus } = useApi(() => libraryApi.getStatus());
  const [platform, setPlatform] = useState<LibraryPlatform>('plex');
  const [settingsStatus, setSettingsStatus] = useState<Settings | null>(null);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditReviewOpen, setAuditReviewOpen] = useState(false);

  const { initFromApi } = useSettingsStore();

  useEffect(() => {
    settingsApi.get().then(s => {
      setSettingsStatus(s);
      initFromApi(s);
      if (s.library_platform) setPlatform(s.library_platform);
    }).catch(() => {
      toast.error('Failed to load settings');
    }).finally(() => {
      setSettingsLoaded(true);
    });
  }, []);

  useEffect(() => {
    libraryBrowseApi.getAudit({ per_page: 1 })
      .then(r => setAuditTotal(r.total))
      .catch(() => {});
  }, []);

  const handleServiceTestResult = (
    label: string,
    result: { connected: boolean; message?: string },
  ) => {
    const detail = result.message?.trim();
    if (result.connected) {
      toast.success(detail ? `${label}: ${detail}` : `${label}: connection OK`);
      return;
    }
    toast.error(detail ? `${label}: ${detail}` : `${label}: connection failed`);
  };

  const refreshAuditTotal = () => {
    libraryBrowseApi.getAudit({ per_page: 1 })
      .then(r => setAuditTotal(r.total))
      .catch(() => {});
  };

  return (
    <div className="py-8 space-y-10">
      <h1 className="page-title">Settings</h1>

      <ConnectionsSection
        platform={platform}
        settingsStatus={settingsStatus}
        settingsLoaded={settingsLoaded}
        onServiceTestResult={handleServiceTestResult}
        toast={toast}
      />

      <CapabilitiesSection toast={toast} />

      <EnrichmentSection
        platform={platform}
        libraryTrackCount={libraryStatus?.track_count}
        libraryLastSynced={libraryStatus?.last_synced}
        auditTotal={auditTotal}
        onOpenAuditReview={() => setAuditReviewOpen(true)}
        toast={toast}
      />

      <AuditReviewModal
        open={auditReviewOpen}
        onClose={() => setAuditReviewOpen(false)}
        onRefreshAuditTotal={refreshAuditTotal}
        toast={toast}
      />

      <SecuritySection toast={toast} />

      <DangerZoneSection toast={toast} />
    </div>
  );
}
