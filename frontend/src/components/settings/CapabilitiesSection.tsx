import { useState } from 'react';
import { settingsApi } from '../../services/api';
import { useSettingsStore } from '../../stores/useSettingsStore';
import { Toggle } from '../common/Toggle';

interface CapabilitiesSectionProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function CapabilitiesSection({ toast }: CapabilitiesSectionProps) {
  const fetchEnabled = useSettingsStore(s => s.fetchEnabled);
  const setFetchEnabled = useSettingsStore(s => s.setFetchEnabled);
  const [fetchToggling, setFetchToggling] = useState(false);

  const handleFetchToggle = async () => {
    setFetchToggling(true);
    try {
      const newValue = !fetchEnabled;
      await settingsApi.setFetchEnabled(newValue);
      setFetchEnabled(newValue);
      toast.success(newValue ? 'Fetch enabled' : 'Fetch disabled');
    } catch {
      toast.error('Failed to update fetch setting');
    } finally {
      setFetchToggling(false);
    }
  };

  return (
    <section className="border-t border-[#1a1a1a] pt-8">
      <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-3">Capabilities</h2>
      <div className="bg-[#0e0e0e] border border-[#1a1a1a] p-4 flex items-center justify-between">
        <div>
          <p className="text-text-primary text-sm font-medium">Enable Fetch</p>
          <p className="text-[#444] text-[10px] mt-0.5">
            Show download actions in the Forge. Requires a downloader plugin (Lidarr, Soulseek).
          </p>
        </div>
        <Toggle
          on={fetchEnabled}
          onChange={fetchToggling ? () => {} : () => void handleFetchToggle()}
        />
      </div>
    </section>
  );
}
