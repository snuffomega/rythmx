import { useEffect, useMemo, useState } from 'react';
import { Download, Layers, Loader2, RefreshCw, Send, Trash2 } from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { useApi } from '../hooks/useApi';
import { forgeBuildsApi, settingsApi } from '../services/api';
import { useToastStore } from '../stores/useToastStore';
import type { ForgeBuild } from '../types';

const SOURCE_LABELS: Record<string, string> = {
  new_music: 'New Music',
  custom_discovery: 'Custom Discovery',
  sync: 'Sync',
  manual: 'Manual',
};

const STATUS_STYLES: Record<string, string> = {
  queued: 'text-[#999] border-[#333]',
  building: 'text-accent border-accent/40',
  ready: 'text-success border-success/40',
  published: 'text-white border-white/30',
  failed: 'text-danger border-danger/40',
};

function BuildCard({
  build,
  selected,
  onSelect,
  onDelete,
  deleting,
}: {
  build: ForgeBuild;
  selected: boolean;
  onSelect: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const sourceLabel = SOURCE_LABELS[build.source] ?? build.source;
  const statusStyle = STATUS_STYLES[build.status] ?? 'text-text-muted border-[#2a2a2a]';
  const created = new Date(build.created_at).toLocaleString();

  return (
    <div
      className={`w-full text-left p-4 border transition-colors ${
        selected ? 'border-accent bg-accent/5' : 'border-[#1a1a1a] bg-[#0e0e0e] hover:border-[#2a2a2a]'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <button onClick={onSelect} className="min-w-0 flex-1 text-left" aria-label={`Open build ${build.name}`}>
          <p className="text-text-primary text-sm font-semibold truncate">{build.name}</p>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            <span className="text-[10px] text-[#555] uppercase tracking-wide">{sourceLabel}</span>
            <span className={`text-[10px] border px-1.5 py-0.5 uppercase tracking-wide ${statusStyle}`}>
              {build.status}
            </span>
          </div>
          <p className="text-[#444] text-xs mt-2">
            {build.item_count} item{build.item_count === 1 ? '' : 's'} | {created}
          </p>
        </button>
        <button
          onClick={onDelete}
          disabled={deleting}
          className="text-[#2a2a2a] hover:text-danger transition-colors p-1"
          aria-label="Delete build"
        >
          {deleting ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
        </button>
      </div>
    </div>
  );
}

export function ForgeBuilder() {
  const navigate = useNavigate();
  const toastSuccess = useToastStore(s => s.success);
  const toastError = useToastStore(s => s.error);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [publishingId, setPublishingId] = useState<string | null>(null);
  const [fetchingId, setFetchingId] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);

  const [editName, setEditName] = useState('');
  const [editStatus, setEditStatus] = useState<ForgeBuild['status']>('ready');
  const [editSummary, setEditSummary] = useState('{}');

  const { data: builds, loading, error, refetch } = useApi(() => forgeBuildsApi.list(undefined, 200));
  const { data: appSettings } = useApi(() => settingsApi.get());
  const fetchEnabled = !!appSettings?.fetch_enabled;

  const orderedBuilds = (builds ?? []) as ForgeBuild[];
  const selectedBuild = useMemo(
    () => orderedBuilds.find(b => b.id === selectedId) ?? orderedBuilds[0] ?? null,
    [orderedBuilds, selectedId]
  );

  useEffect(() => {
    if (!selectedBuild) {
      setEditName('');
      setEditStatus('ready');
      setEditSummary('{}');
      return;
    }
    setEditName(selectedBuild.name || '');
    setEditStatus(selectedBuild.status);
    setEditSummary(JSON.stringify(selectedBuild.summary ?? {}, null, 2));
  }, [selectedBuild?.id, selectedBuild?.updated_at]);

  const extractErrorMessage = (err: unknown, fallback: string) => {
    if (!(err instanceof Error)) return fallback;
    const raw = err.message || '';
    if (raw.trim().startsWith('{')) {
      try {
        const parsed = JSON.parse(raw) as { message?: string; error?: string };
        return parsed.message || parsed.error || fallback;
      } catch {
        return raw || fallback;
      }
    }
    return raw || fallback;
  };

  const handleDelete = async (build: ForgeBuild) => {
    setDeletingId(build.id);
    try {
      await forgeBuildsApi.delete(build.id);
      toastSuccess(`Deleted build "${build.name}"`);
      if (selectedId === build.id) {
        setSelectedId(null);
      }
      refetch();
    } catch {
      toastError('Failed to delete build');
    } finally {
      setDeletingId(null);
    }
  };

  const handlePublish = async (build: ForgeBuild) => {
    setPublishingId(build.id);
    try {
      const result = await forgeBuildsApi.publish(build.id, build.name);
      toastSuccess(
        `Published "${result.playlist.name}" (${result.playlist.track_count} tracks) to ${result.platform}`
      );
      refetch();
      navigate({ to: '/library/playlists' });
    } catch (err) {
      toastError(extractErrorMessage(err, 'Failed to publish build'));
    } finally {
      setPublishingId(null);
    }
  };

  const handleFetch = async (build: ForgeBuild) => {
    setFetchingId(build.id);
    try {
      const result = await forgeBuildsApi.fetch(build.id);
      toastSuccess(result.message || 'Fetch started');
    } catch (err) {
      toastError(extractErrorMessage(err, 'Failed to start fetch'));
    } finally {
      setFetchingId(null);
    }
  };

  const originalSummaryText = selectedBuild
    ? JSON.stringify(selectedBuild.summary ?? {}, null, 2)
    : '{}';
  const normalizedName = editName.trim();
  const isDirty = !!selectedBuild && (
    normalizedName !== (selectedBuild.name || '').trim() ||
    editStatus !== selectedBuild.status ||
    editSummary.trim() !== originalSummaryText.trim()
  );

  const handleSave = async (build: ForgeBuild) => {
    let parsedSummary: Record<string, unknown>;
    try {
      parsedSummary = JSON.parse(editSummary || '{}') as Record<string, unknown>;
      if (!parsedSummary || Array.isArray(parsedSummary) || typeof parsedSummary !== 'object') {
        throw new Error('Summary must be a JSON object');
      }
    } catch {
      toastError('Summary must be valid JSON object syntax');
      return;
    }

    setSavingId(build.id);
    try {
      const updated = await forgeBuildsApi.update(build.id, {
        name: normalizedName || build.name,
        status: editStatus,
        summary: parsedSummary,
      });
      toastSuccess(`Saved "${updated.name}"`);
      setEditName(updated.name || '');
      setEditStatus(updated.status);
      setEditSummary(JSON.stringify(updated.summary ?? {}, null, 2));
      refetch();
    } catch (err) {
      toastError(extractErrorMessage(err, 'Failed to save build'));
    } finally {
      setSavingId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">Builder</h2>
          <p className="text-text-muted text-sm mt-1">
            Manage and publish your builds. Runs from New Music, Custom Discovery, and Sync appear here.
          </p>
        </div>
        <button
          onClick={refetch}
          className="btn-secondary flex items-center gap-2 text-xs"
          disabled={loading}
        >
          {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          Refresh
        </button>
      </div>

      {error && (
        <div className="border border-danger/40 bg-danger/5 p-3">
          <p className="text-danger text-xs">{String(error)}</p>
        </div>
      )}

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-20 bg-[#0d0d0d] border border-[#1a1a1a] animate-pulse" />
          ))}
        </div>
      ) : orderedBuilds.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 gap-4 border border-dashed border-[#1a1a1a]">
          <Layers size={40} className="text-[#2a2a2a]" />
          <p className="text-text-muted text-sm">No builds yet</p>
          <p className="text-[#444] text-xs text-center max-w-xs">
            Run New Music, Custom Discovery, or Sync to queue a build here
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4">
          <div className="space-y-3">
            {orderedBuilds.map(build => (
              <BuildCard
                key={build.id}
                build={build}
                selected={selectedBuild?.id === build.id}
                onSelect={() => setSelectedId(build.id)}
                onDelete={() => handleDelete(build)}
                deleting={deletingId === build.id}
              />
            ))}
          </div>

          <div className="bg-[#0e0e0e] border border-[#1a1a1a] p-4">
            {!selectedBuild ? (
              <p className="text-[#444] text-sm">Select a build to inspect details.</p>
            ) : (
              <div className="space-y-4">
                <div>
                  <p className="text-text-primary text-sm font-semibold">{selectedBuild.name}</p>
                  <p className="text-[#444] text-xs mt-1">
                    ID: {selectedBuild.id} | {selectedBuild.source} | {selectedBuild.status}
                  </p>
                </div>

                <div className="space-y-3 bg-[#0b0b0b] border border-[#1a1a1a] p-3">
                  <div>
                    <p className="text-text-muted text-xs uppercase tracking-wide mb-1.5">Build Name</p>
                    <input
                      value={editName}
                      onChange={e => setEditName(e.target.value)}
                      className="w-full bg-[#111] border border-[#2a2a2a] text-text-primary text-sm px-3 py-1.5 focus:outline-none focus:border-accent"
                    />
                  </div>

                  <div>
                    <p className="text-text-muted text-xs uppercase tracking-wide mb-1.5">Status</p>
                    <select
                      value={editStatus}
                      onChange={e => setEditStatus(e.target.value as ForgeBuild['status'])}
                      className="bg-[#111] border border-[#2a2a2a] text-text-primary text-sm px-3 py-1.5 focus:outline-none focus:border-accent"
                    >
                      <option value="queued">queued</option>
                      <option value="building">building</option>
                      <option value="ready">ready</option>
                      <option value="published">published</option>
                      <option value="failed">failed</option>
                    </select>
                  </div>

                  <div>
                    <p className="text-text-muted text-xs uppercase tracking-wide mb-1.5">Summary (JSON Object)</p>
                    <textarea
                      value={editSummary}
                      onChange={e => setEditSummary(e.target.value)}
                      rows={8}
                      className="w-full bg-[#111] border border-[#2a2a2a] text-text-primary text-xs font-mono px-3 py-2 focus:outline-none focus:border-accent"
                    />
                  </div>

                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => handleSave(selectedBuild)}
                      disabled={!isDirty || savingId === selectedBuild.id}
                      className="btn-secondary inline-flex items-center gap-2 text-xs w-fit disabled:opacity-40"
                    >
                      {savingId === selectedBuild.id ? <Loader2 size={12} className="animate-spin" /> : null}
                      Save
                    </button>
                    {!isDirty && <span className="text-[#444] text-[11px]">No unsaved changes</span>}
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  {fetchEnabled && (
                    <button
                      onClick={() => handleFetch(selectedBuild)}
                      disabled={fetchingId === selectedBuild.id}
                      className="btn-secondary inline-flex items-center gap-2 text-xs w-fit"
                    >
                      {fetchingId === selectedBuild.id ? (
                        <Loader2 size={12} className="animate-spin" />
                      ) : (
                        <Download size={12} />
                      )}
                      Fetch
                    </button>
                  )}
                  <button
                    onClick={() => handlePublish(selectedBuild)}
                    disabled={publishingId === selectedBuild.id}
                    className="btn-primary inline-flex items-center gap-2 text-xs w-fit"
                  >
                    {publishingId === selectedBuild.id ? (
                      <Loader2 size={12} className="animate-spin" />
                    ) : (
                      <Send size={12} />
                    )}
                    Publish
                  </button>
                </div>

                <div>
                  <p className="text-text-muted text-xs uppercase tracking-wide mb-2">Saved Summary</p>
                  <pre className="text-[11px] text-[#666] bg-[#0b0b0b] border border-[#1a1a1a] p-3 overflow-auto max-h-40">
{JSON.stringify(selectedBuild.summary ?? {}, null, 2)}
                  </pre>
                </div>

                <div>
                  <p className="text-text-muted text-xs uppercase tracking-wide mb-2">Items ({selectedBuild.item_count})</p>
                  <pre className="text-[11px] text-[#666] bg-[#0b0b0b] border border-[#1a1a1a] p-3 overflow-auto max-h-72">
{JSON.stringify((selectedBuild.track_list ?? []).slice(0, 30), null, 2)}
                  </pre>
                  {selectedBuild.item_count > 30 && (
                    <p className="text-[#444] text-[11px] mt-2">Showing first 30 items.</p>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

