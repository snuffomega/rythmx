import { useState } from 'react';
import { Link2, Loader2 } from 'lucide-react';
import { useToastStore } from '../stores/useToastStore';

export function ForgeSync() {
  const toast = {
    info: useToastStore(s => s.info),
    error: useToastStore(s => s.error),
  };

  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);

  const handleLoad = async () => {
    const trimmed = url.trim();
    if (!trimmed) return;
    setLoading(true);
    try {
      // Backend wiring: POST /api/v1/forge/sync/load — Phase 27d
      toast.info('URL sync backend coming in Phase 27d');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-text-primary">Sync from URL</h2>
        <p className="text-text-muted text-sm mt-1">
          Paste a playlist URL to import and resolve its tracks against your library, then queue it in Builder.
        </p>
      </div>

      <div className="bg-[#0e0e0e] border border-[#1a1a1a] p-5 space-y-4">
        <div className="flex gap-2">
          <div className="flex-1 relative">
            <Link2 size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#444]" />
            <input
              type="url"
              value={url}
              onChange={e => setUrl(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleLoad()}
              placeholder="https://open.spotify.com/playlist/..."
              className="w-full bg-[#111] border border-[#2a2a2a] text-text-primary text-sm pl-9 pr-3 py-2 placeholder:text-[#333] focus:outline-none focus:border-accent"
            />
          </div>
          <button
            onClick={handleLoad}
            disabled={loading || !url.trim()}
            className="px-4 py-2 text-sm font-semibold bg-[#1e1e1e] border border-[#2a2a2a] text-text-primary hover:border-accent transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {loading && <Loader2 size={13} className="animate-spin" />}
            Load
          </button>
        </div>
        <p className="text-[#444] text-xs">
          Supported: Spotify, YouTube Music playlist URLs · File import (M3U, CSV) coming later
        </p>
      </div>
    </div>
  );
}
