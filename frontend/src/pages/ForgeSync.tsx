import { useEffect, useMemo, useState } from 'react';
import { Link2, Loader2 } from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { useToastStore } from '../stores/useToastStore';
import { forgeSyncApi } from '../services/api';

export function ForgeSync() {
  const navigate = useNavigate();
  const toast = {
    success: useToastStore(s => s.success),
    error: useToastStore(s => s.error),
    info: useToastStore(s => s.info),
  };

  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [batchMode, setBatchMode] = useState(true);
  const [chunkSize, setChunkSize] = useState(500);
  const [firstN, setFirstN] = useState(500);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [job, setJob] = useState<{
    status: 'queued' | 'running' | 'completed' | 'failed';
    processed_tracks: number;
    total_tracks: number;
    completed_chunks: number;
    total_chunks: number;
    owned_count: number;
    missing_count: number;
    message?: string;
    error?: string | null;
  } | null>(null);

  const percent = useMemo(() => {
    if (!job || job.total_tracks <= 0) return 0;
    return Math.max(0, Math.min(100, Math.round((job.processed_tracks / job.total_tracks) * 100)));
  }, [job]);

  useEffect(() => {
    if (!activeJobId) return;

    let cancelled = false;
    let handledTerminal = false;

    const poll = async () => {
      try {
        const result = await forgeSyncApi.getJob(activeJobId);
        if (cancelled) return;
        setJob(result.job);

        if ((result.job.status === 'completed' || result.job.status === 'failed') && !handledTerminal) {
          handledTerminal = true;
          setActiveJobId(null);
          if (result.job.status === 'completed') {
            toast.success(
              `Batch sync complete: ${result.job.total_tracks} tracks (${result.job.owned_count} owned)`
            );
            setTimeout(() => navigate({ to: '/forge/builder' }), 500);
          } else {
            toast.error(result.job.error || 'Batch sync failed');
          }
        }
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : 'Failed to read batch status';
        toast.error(message);
        setActiveJobId(null);
      }
    };

    void poll();
    const timer = window.setInterval(() => {
      void poll();
    }, 2000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeJobId, navigate, toast]);

  const handleLoad = async () => {
    const trimmed = url.trim();
    if (!trimmed) return;
    setLoading(true);
    try {
      const result = await forgeSyncApi.load({
        source_url: trimmed,
        queue_build: true,
        batch_mode: batchMode,
        chunk_size: batchMode ? chunkSize : undefined,
        max_tracks: batchMode ? undefined : Math.max(1, Number(firstN) || 1),
      });
      if (result.mode === 'batch' && result.job_id) {
        setActiveJobId(result.job_id);
        setJob({
          status: 'queued',
          processed_tracks: 0,
          total_tracks: 0,
          completed_chunks: 0,
          total_chunks: 0,
          owned_count: 0,
          missing_count: 0,
          message: 'Queued',
          error: null,
        });
        toast.info('Batch sync started in background. You can keep using the app.');
      } else {
        const sourceTotal = Number(result.source_track_count || result.track_count || 0);
        const loadedTotal = Number(result.track_count || 0);
        const wasTrimmed = sourceTotal > loadedTotal;
        toast.success(
          wasTrimmed
            ? `Loaded first ${loadedTotal}/${sourceTotal} tracks (${result.owned_count} owned) and queued build in Builder`
            : `Loaded ${loadedTotal} tracks (${result.owned_count} owned) and queued build in Builder`
        );
        setTimeout(() => navigate({ to: '/forge/builder' }), 400);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load source URL';
      toast.error(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-text-primary">Sync from URL</h2>
        <p className="text-text-muted text-sm mt-1">
          Paste a source playlist URL to resolve tracks against your library, then queue a build in Builder.
        </p>
      </div>

      <div className="bg-base border border-border-subtle p-5 space-y-4">
        <div className="flex flex-wrap items-center gap-4">
          <label className="inline-flex items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={batchMode}
              onChange={e => setBatchMode(e.target.checked)}
              className="accent-accent"
            />
            Batch Mode (background)
          </label>
          {batchMode && (
            <label className="inline-flex items-center gap-2 text-sm text-text-primary">
              Chunk Size
              <select
                value={chunkSize}
                onChange={e => setChunkSize(Number(e.target.value))}
                className="bg-surface border border-border-input text-text-primary text-sm px-2 py-1 focus:outline-none focus:border-accent"
              >
                <option value={250}>250</option>
                <option value={500}>500</option>
                <option value={750}>750</option>
                <option value={1000}>1000</option>
              </select>
            </label>
          )}
          {!batchMode && (
            <label className="inline-flex items-center gap-2 text-sm text-text-primary">
              First N Tracks
              <input
                type="number"
                min={1}
                max={10000}
                step={1}
                value={firstN}
                onChange={e => setFirstN(Number(e.target.value || 1))}
                className="w-24 bg-surface border border-border-input text-text-primary text-sm px-2 py-1 focus:outline-none focus:border-accent"
              />
            </label>
          )}
        </div>

        <div className="flex gap-2">
          <div className="flex-1 relative">
            <Link2 size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-dim" />
            <input
              type="url"
              value={url}
              onChange={e => setUrl(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleLoad()}
              placeholder="https://open.spotify.com/playlist/..."
              className="w-full bg-surface border border-border-input text-text-primary text-sm pl-9 pr-3 py-2 placeholder:text-text-faint focus:outline-none focus:border-accent"
            />
          </div>
          <button
            onClick={handleLoad}
            disabled={loading || !url.trim()}
            className="px-4 py-2 text-sm font-semibold bg-surface-overlay border border-border-input text-text-primary hover:border-accent transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {loading && <Loader2 size={13} className="animate-spin" />}
            Load
          </button>
        </div>
        {batchMode && job && (
          <div className="bg-surface-sunken border border-border-subtle p-3 space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-text-primary uppercase tracking-wide">Batch Status: {job.status}</span>
              <span className="text-text-muted">{percent}%</span>
            </div>
            <div className="h-2 bg-surface-skeleton border border-border-subtle">
              <div className="h-full bg-accent" style={{ width: `${percent}%` }} />
            </div>
            <p className="text-text-muted text-xs">
              {job.message || 'Processing'} | Tracks {job.processed_tracks}/{job.total_tracks || '?'} | Chunks{' '}
              {job.completed_chunks}/{job.total_chunks || '?'}
            </p>
          </div>
        )}
        <p className="text-text-dim text-xs">
          Supported: Spotify, Last.fm, and Deezer playlist URLs | File import (M3U, CSV) coming later
        </p>
      </div>
    </div>
  );
}
