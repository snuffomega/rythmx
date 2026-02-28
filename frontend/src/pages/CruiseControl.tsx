import { useState, useEffect, useRef } from 'react';
import { Zap, Sparkles } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { cruiseControlApi, releaseCacheApi } from '../services/api';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { CCConfigForm, CCHistory } from '../components/cc';
import { PersonalDiscovery } from './PersonalDiscovery';
import type { CruiseControlConfig } from '../types';

type CCTab = 'new-music' | 'personal-discovery';

interface CruiseControlProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function CruiseControl({ toast }: CruiseControlProps) {
  const { data: config, loading: configLoading } = useApi(() => cruiseControlApi.getConfig());
  const { data: history, loading: historyLoading } = useApi(() => cruiseControlApi.getHistory());
  const { data: statusData, refetch: refetchStatus } = useApi(() => cruiseControlApi.getStatus());

  const [tab, setTab] = useState<CCTab>('new-music');
  const [form, setForm] = useState<Partial<CruiseControlConfig>>({});
  const [advanced, setAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  useEffect(() => {
    if (config) setForm(config);
  }, [config]);

  // Poll status every 2s while a run is active
  useEffect(() => {
    if (statusData?.state === 'running') {
      const interval = setInterval(refetchStatus, 2000);
      return () => clearInterval(interval);
    }
  }, [statusData?.state, refetchStatus]);

  // Toast on run completion
  const prevStateRef = useRef<string | undefined>();
  useEffect(() => {
    if (prevStateRef.current === 'running' && statusData?.state === 'completed') {
      const s = statusData.summary;
      toast.success(`Run complete — ${s?.owned ?? 0} owned · ${s?.queued ?? 0} queued`);
    }
    prevStateRef.current = statusData?.state;
  }, [statusData?.state]);

  const update = (key: keyof CruiseControlConfig, value: unknown) =>
    setForm(f => ({ ...f, [key]: value }));

  const handleSave = async () => {
    setSaving(true);
    try {
      await cruiseControlApi.saveConfig(form);
      toast.success('Config saved');
    } catch {
      toast.error('Failed to save config');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAndRun = async () => {
    setSaving(true);
    try {
      await cruiseControlApi.saveConfig(form);
      const runMode = form.cc_dry_run ? 'dry' : (form.cc_run_mode ?? 'playlist');
      await cruiseControlApi.runNow(runMode);
      toast.success('Config saved and run started');
      refetchStatus();
    } catch {
      toast.error('Failed to save and run');
    } finally {
      setSaving(false);
    }
  };

  const handleClearCache = async () => {
    try {
      await releaseCacheApi.clear();
      toast.success('Release cache cleared');
    } catch {
      toast.error('Failed to clear cache');
    }
    setConfirmClear(false);
  };

  return (
    <div className="py-8 space-y-8">
      <div className="space-y-6">
        <div>
          <h1 className="page-title">Cruise Control</h1>
          <p className="text-text-muted text-sm mt-1">Automate and find the music you love</p>
        </div>

        <div className="flex items-center gap-1.5 bg-[#0e0e0e] border border-[#1a1a1a] p-1.5 w-fit">
          <button
            onClick={() => setTab('new-music')}
            className={`flex items-center gap-2 px-5 py-2.5 text-sm font-semibold transition-all duration-150 ${
              tab === 'new-music'
                ? 'bg-[#1e1e1e] text-text-primary shadow-sm border border-[#2a2a2a]'
                : 'text-[#3a3a3a] hover:text-[#666]'
            }`}
          >
            <Zap size={14} className={tab === 'new-music' ? 'text-accent' : ''} />
            New Music
          </button>
          <button
            onClick={() => setTab('personal-discovery')}
            className={`flex items-center gap-2 px-5 py-2.5 text-sm font-semibold transition-all duration-150 ${
              tab === 'personal-discovery'
                ? 'bg-[#1e1e1e] text-text-primary shadow-sm border border-[#2a2a2a]'
                : 'text-[#3a3a3a] hover:text-[#666]'
            }`}
          >
            <Sparkles size={14} className={tab === 'personal-discovery' ? 'text-accent' : ''} />
            Personal Discovery
          </button>
        </div>
      </div>

      {tab === 'personal-discovery' && <PersonalDiscovery toast={toast} />}

      {tab === 'new-music' && <>
        {configLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-10 bg-[#1c1c1c] animate-pulse" />
            ))}
          </div>
        ) : (
          <CCConfigForm
            form={form}
            update={update}
            advanced={advanced}
            onAdvancedToggle={() => setAdvanced(a => !a)}
            saving={saving}
            onSave={handleSave}
            onSaveAndRun={handleSaveAndRun}
            onRequestClearCache={() => setConfirmClear(true)}
            statusData={statusData}
          />
        )}

        <CCHistory history={history} loading={historyLoading} />

        <ConfirmDialog
          open={confirmClear}
          title="Clear Release Cache?"
          description="This will remove all cached release data. Cruise Control will re-fetch everything on its next run."
          confirmLabel="Clear Cache"
          danger
          onConfirm={handleClearCache}
          onCancel={() => setConfirmClear(false)}
        />
      </>}
    </div>
  );
}
