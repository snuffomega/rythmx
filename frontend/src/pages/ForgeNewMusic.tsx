import { useState, useEffect, useRef } from 'react';
import { useApi } from '../hooks/useApi';
import { cruiseControlApi, releaseCacheApi } from '../services/api';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ApiErrorBanner } from '../components/common';
import { NewMusicConfigForm, RunHistory, PipelineRunHistory } from '../components/forge';
import { useToastStore } from '../stores/useToastStore';
import type { CruiseControlConfig } from '../types';

export function ForgeNewMusic() {
  const toast = {
    success: useToastStore(s => s.success),
    error: useToastStore(s => s.error),
  };

  const { data: config, loading: configLoading, error: configError, refetch: refetchConfig } = useApi(() => cruiseControlApi.getConfig());
  const { data: history, loading: historyLoading, error: historyError, refetch: refetchHistory } = useApi(() => cruiseControlApi.getHistory());
  const { data: statusData, refetch: refetchStatus } = useApi(() => cruiseControlApi.getStatus());

  const [form, setForm] = useState<Partial<CruiseControlConfig>>({});
  const [advanced, setAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  useEffect(() => {
    if (config) setForm(config);
  }, [config]);

  // Poll every 2s while running
  useEffect(() => {
    if (statusData?.state === 'running') {
      const interval = setInterval(refetchStatus, 2000);
      return () => clearInterval(interval);
    }
  }, [statusData?.state, refetchStatus]);

  // Toast on completion
  const prevStateRef = useRef<string | undefined>();
  useEffect(() => {
    if (prevStateRef.current === 'running' && statusData?.state === 'completed') {
      const s = statusData.summary;
      toast.success(`Run complete — ${s?.owned ?? 0} owned · ${s?.queued ?? 0} queued`);
      refetchHistory();
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
      const runMode = form.dry_run ? 'preview' : (form.run_mode ?? 'build');
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
    <div className="space-y-0">
      {/* Workspace: config form */}
      {configError ? (
        <ApiErrorBanner error={configError} onRetry={refetchConfig} />
      ) : configLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-10 bg-[#1c1c1c] animate-pulse" />
          ))}
        </div>
      ) : (
        <NewMusicConfigForm
          form={form}
          update={update}
          advanced={advanced}
          onAdvancedToggle={() => setAdvanced(a => !a)}
          saving={saving}
          onSave={handleSave}
          onSaveAndRun={handleSaveAndRun}
          onRequestClearCache={() => setConfirmClear(true)}
          statusData={statusData ?? null}
        />
      )}

      {/* History ledger: run-level + release-level */}
      <PipelineRunHistory pipelineType="new_music" />

      {historyError ? (
        <ApiErrorBanner error={historyError} onRetry={refetchHistory} />
      ) : (
        <RunHistory history={history} loading={historyLoading} />
      )}

      <ConfirmDialog
        open={confirmClear}
        title="Clear Release Cache?"
        description="This will remove all cached release data. New Music will re-fetch everything on its next run."
        confirmLabel="Clear Cache"
        danger
        onConfirm={handleClearCache}
        onCancel={() => setConfirmClear(false)}
      />
    </div>
  );
}
