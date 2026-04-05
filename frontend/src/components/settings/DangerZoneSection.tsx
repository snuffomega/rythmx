import { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { settingsApi } from '../../services/api';
import { ConfirmDialog } from '../ConfirmDialog';

interface DangerZoneSectionProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function DangerZoneSection({ toast }: DangerZoneSectionProps) {
  const [dangerOpen, setDangerOpen] = useState(false);
  const [confirmClearHistory, setConfirmClearHistory] = useState(false);
  const [confirmResetDb, setConfirmResetDb] = useState(false);

  const handleClearHistory = async () => {
    try {
      await settingsApi.clearHistory();
      toast.success('History cleared');
    } catch {
      toast.error('Failed to clear history');
    }
    setConfirmClearHistory(false);
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
              <p className="text-[#444] text-xs mt-0.5">Remove all New Music run history</p>
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

      <ConfirmDialog
        open={confirmClearHistory}
        title="Clear History?"
        description="All New Music run history will be permanently deleted. This cannot be undone."
        confirmLabel="Clear History"
        danger
        onConfirm={() => void handleClearHistory()}
        onCancel={() => setConfirmClearHistory(false)}
      />

      <ConfirmDialog
        open={confirmResetDb}
        title="Reset Database?"
        description="This will permanently delete ALL app data including playlists, queue items, and history. There is no recovery. Are you absolutely sure?"
        confirmLabel="Reset Everything"
        danger
        onConfirm={() => void handleResetDb()}
        onCancel={() => setConfirmResetDb(false)}
      />
    </section>
  );
}
