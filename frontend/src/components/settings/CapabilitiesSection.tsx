import { useState } from 'react';
import { settingsApi } from '../../services/api';
import { useSettingsStore } from '../../stores/useSettingsStore';

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
        <button
          onClick={() => void handleFetchToggle()}
          disabled={fetchToggling}
          className={`relative inline-flex items-center w-10 h-5 rounded-full transition-colors duration-200 flex-shrink-0 ${
            fetchEnabled ? 'bg-accent' : 'bg-[#2a2a2a]'
          } ${fetchToggling ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
          aria-label={fetchEnabled ? 'Disable fetch' : 'Enable fetch'}
        >
          <span
            className={`inline-block w-4 h-4 rounded-full bg-white shadow transition-transform duration-200 ${
              fetchEnabled ? 'translate-x-5' : 'translate-x-0.5'
            }`}
          />
        </button>
      </div>
    </section>
  );
}
