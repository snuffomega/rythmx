import { useEffect, useMemo, useState } from 'react';
import { Download, Layers, Loader2, RefreshCw, RotateCcw, Send, Trash2, X } from 'lucide-react';
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

const SYNC_SOURCE_STYLES: Record<string, string> = {
  deezer: 'text-[#d2c3ff] border-[#8f69ff]/60 bg-[#8f69ff]/15',
  spotify: 'text-[#8af2b3] border-[#1db954]/60 bg-[#1db954]/15',
  lastfm: 'text-[#ffaca7] border-[#d51007]/60 bg-[#d51007]/15',
};

function toStringValue(value: unknown): string {
  if (value === null || value === undefined) return '-';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) return `${value.length} items`;
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function toBool(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value > 0;
  if (typeof value === 'string') return ['1', 'true', 'yes', 'on'].includes(value.toLowerCase());
  return false;
}

function prettyKey(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, ch => ch.toUpperCase());
}

function BuildCard({
  build,
  selected,
  onSelect,
  onDelete,
  deleting,
  onResync,
  resyncing,
}: {
  build: ForgeBuild;
  selected: boolean;
  onSelect: () => void;
  onDelete: () => void;
  deleting: boolean;
  onResync: () => void;
  resyncing: boolean;
}) {
  const sourceLabel = SOURCE_LABELS[build.source] ?? build.source;
  const statusStyle = STATUS_STYLES[build.status] ?? 'text-text-muted border-[#2a2a2a]';
  const created = new Date(build.created_at).toLocaleString();
  const syncSource =
    build.source === 'sync' ? String((build.summary?.source as string | undefined) || '').toLowerCase() : '';
  const syncSourceStyle = SYNC_SOURCE_STYLES[syncSource] ?? 'text-[#aaa] border-[#2a2a2a] bg-transparent';

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
            {syncSource && (
              <span className={`text-[10px] border px-1.5 py-0.5 uppercase tracking-wide ${syncSourceStyle}`}>
                {syncSource}
              </span>
            )}
            <span className={`text-[10px] border px-1.5 py-0.5 uppercase tracking-wide ${statusStyle}`}>
              {build.status}
            </span>
          </div>
          <p className="text-[#444] text-xs mt-2">
            {build.item_count} item{build.item_count === 1 ? '' : 's'} | {created}
          </p>
        </button>
        <div className="flex items-center gap-1">
          {build.source === 'sync' && (
            <button
              onClick={onResync}
              disabled={resyncing}
              className="text-[#2a2a2a] hover:text-accent transition-colors p-1 disabled:opacity-40"
              aria-label="Re-sync build"
              title="Re-sync build"
            >
              {resyncing ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />}
            </button>
          )}
          <button
            onClick={onDelete}
            disabled={deleting}
            className="text-[#2a2a2a] hover:text-danger transition-colors p-1"
            aria-label="Delete build"
            title="Delete build"
          >
            {deleting ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
          </button>
        </div>
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
  const [resyncingId, setResyncingId] = useState<string | null>(null);
  const [itemsModalBuildId, setItemsModalBuildId] = useState<string | null>(null);
  const [itemDraft, setItemDraft] = useState<Array<Record<string, unknown>>>([]);
  const [savingItemsBuildId, setSavingItemsBuildId] = useState<string | null>(null);

  const [editName, setEditName] = useState('');
  const [editStatus, setEditStatus] = useState<ForgeBuild['status']>('ready');

  const { data: builds, loading, error, refetch } = useApi(() => forgeBuildsApi.list(undefined, 200));
  const { data: appSettings } = useApi(() => settingsApi.get());
  const fetchEnabled = !!appSettings?.fetch_enabled;

  const orderedBuilds = (builds ?? []) as ForgeBuild[];
  const selectedBuild = useMemo(
    () => orderedBuilds.find(b => b.id === selectedId) ?? orderedBuilds[0] ?? null,
    [orderedBuilds, selectedId]
  );
  const itemsModalBuild = useMemo(
    () => orderedBuilds.find(b => b.id === itemsModalBuildId) ?? null,
    [orderedBuilds, itemsModalBuildId]
  );

  useEffect(() => {
    if (!selectedBuild) {
      setEditName('');
      setEditStatus('ready');
      return;
    }
    setEditName(selectedBuild.name || '');
    setEditStatus(selectedBuild.status);
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
      toastError(extractErrorMessage(err, 'Failed to run build'));
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
      toastError(extractErrorMessage(err, 'Failed to start build fetch'));
    } finally {
      setFetchingId(null);
    }
  };

  const handleResync = async (build: ForgeBuild) => {
    setResyncingId(build.id);
    try {
      const result = await forgeBuildsApi.resync(build.id);
      toastSuccess(
        `Re-synced ${result.track_count} tracks (${result.owned_count} owned, ${result.missing_count} missing)`
      );
      refetch();
    } catch (err) {
      toastError(extractErrorMessage(err, 'Failed to re-sync build'));
    } finally {
      setResyncingId(null);
    }
  };

  const normalizedName = editName.trim();
  const isDirty = !!selectedBuild && (
    normalizedName !== (selectedBuild.name || '').trim() ||
    editStatus !== selectedBuild.status
  );

  const handleSave = async (build: ForgeBuild) => {
    setSavingId(build.id);
    try {
      const updated = await forgeBuildsApi.update(build.id, {
        name: normalizedName || build.name,
        status: editStatus,
      });
      toastSuccess(`Saved "${updated.name}"`);
      setEditName(updated.name || '');
      setEditStatus(updated.status);
      refetch();
    } catch (err) {
      toastError(extractErrorMessage(err, 'Failed to save build'));
    } finally {
      setSavingId(null);
    }
  };

  const summaryRows = useMemo(() => {
    if (!selectedBuild) return [] as Array<{ key: string; label: string; value: string }>;
    const summary = selectedBuild.summary ?? {};

    const preferredOrder = [
      'source',
      'track_count',
      'owned_count',
      'missing_count',
      'artists_checked',
      'releases_found',
      'artists_found',
      'seed_period',
      'max_tracks',
      'closeness',
      'source_url',
    ];

    const known = preferredOrder
      .filter(key => key in summary)
      .map(key => ({ key, label: prettyKey(key), value: toStringValue(summary[key]) }));

    const extras = Object.entries(summary)
      .filter(([k]) => !preferredOrder.includes(k))
      .map(([key, value]) => ({ key, label: prettyKey(key), value: toStringValue(value) }));

    return [...known, ...extras];
  }, [selectedBuild]);

  const itemPreview = useMemo(() => {
    if (!selectedBuild) return [] as Array<Record<string, unknown>>;
    return (selectedBuild.track_list ?? []).slice(0, 60) as Array<Record<string, unknown>>;
  }, [selectedBuild]);

  const itemsDirty = useMemo(() => {
    if (!itemsModalBuild) return false;
    const originalCount = (itemsModalBuild.track_list ?? []).length;
    return itemDraft.length !== originalCount;
  }, [itemsModalBuild, itemDraft.length]);

  const openItemsModal = (build: ForgeBuild) => {
    const current = (build.track_list ?? []) as Array<Record<string, unknown>>;
    setItemDraft(current.map(item => ({ ...item })));
    setItemsModalBuildId(build.id);
  };

  const closeItemsModal = () => {
    setItemsModalBuildId(null);
    setItemDraft([]);
  };

  const removeDraftItem = (index: number) => {
    setItemDraft(prev => prev.filter((_, i) => i !== index));
  };

  const saveItemsDraft = async () => {
    if (!itemsModalBuild) return;

    setSavingItemsBuildId(itemsModalBuild.id);
    try {
      const originalSummary = itemsModalBuild.summary ?? {};
      const nextSummary: Record<string, unknown> = { ...originalSummary };
      if (itemsModalBuild.source === 'sync') {
        const ownedCount = itemDraft.reduce((acc, item) => acc + (toBool(item.is_owned) ? 1 : 0), 0);
        nextSummary.track_count = itemDraft.length;
        nextSummary.owned_count = ownedCount;
        nextSummary.missing_count = Math.max(0, itemDraft.length - ownedCount);
      }

      const updated = await forgeBuildsApi.update(itemsModalBuild.id, {
        track_list: itemDraft,
        summary: nextSummary,
      });
      toastSuccess(`Saved ${updated.item_count} build item${updated.item_count === 1 ? '' : 's'}`);
      closeItemsModal();
      refetch();
    } catch (err) {
      toastError(extractErrorMessage(err, 'Failed to save build items'));
    } finally {
      setSavingItemsBuildId(null);
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
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-4">
          <div className="space-y-3 lg:max-h-[calc(100vh-260px)] lg:overflow-y-auto lg:pr-1">
            {orderedBuilds.map(build => (
              <BuildCard
                key={build.id}
                build={build}
                selected={selectedBuild?.id === build.id}
                onSelect={() => setSelectedId(build.id)}
                onDelete={() => handleDelete(build)}
                deleting={deletingId === build.id}
                onResync={() => handleResync(build)}
                resyncing={resyncingId === build.id}
              />
            ))}
          </div>

          <div className="bg-[#0e0e0e] border border-[#1a1a1a] p-4 lg:max-h-[calc(100vh-260px)] lg:overflow-y-auto">
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

                <div className="flex items-center gap-2 flex-wrap">
                  {selectedBuild.source === 'sync' && (
                    <button
                      onClick={() => handleResync(selectedBuild)}
                      disabled={resyncingId === selectedBuild.id}
                      className="btn-secondary inline-flex items-center gap-2 text-xs w-fit"
                    >
                      {resyncingId === selectedBuild.id ? (
                        <Loader2 size={12} className="animate-spin" />
                      ) : (
                        <RotateCcw size={12} />
                      )}
                      Re-sync Source
                    </button>
                  )}
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
                      Build & Fetch
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
                    Build & Save
                  </button>
                  {resyncingId === selectedBuild.id && (
                    <span className="text-[#777] text-[11px]">Re-syncing source and refreshing items...</span>
                  )}
                </div>

                <div>
                  <p className="text-text-muted text-xs uppercase tracking-wide mb-2">Build Summary</p>
                  {summaryRows.length === 0 ? (
                    <div className="text-[12px] text-[#555] bg-[#0b0b0b] border border-[#1a1a1a] p-3">No summary data</div>
                  ) : (
                    <div className="bg-[#0b0b0b] border border-[#1a1a1a] p-3 grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {summaryRows.map(row => (
                        <div key={row.key} className="text-[12px]">
                          <p className="text-[#666] uppercase tracking-wide text-[10px]">{row.label}</p>
                          <p className="text-text-primary break-all">{row.value}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div>
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <p className="text-text-muted text-xs uppercase tracking-wide">
                      Items ({selectedBuild.item_count})
                    </p>
                    <button
                      onClick={() => openItemsModal(selectedBuild)}
                      className="btn-secondary text-[11px] px-2 py-1"
                      disabled={selectedBuild.item_count === 0}
                    >
                      View All & Edit
                    </button>
                  </div>
                  {itemPreview.length === 0 ? (
                    <div className="text-[12px] text-[#555] bg-[#0b0b0b] border border-[#1a1a1a] p-3">No items</div>
                  ) : selectedBuild.source === 'sync' ? (
                    <div className="bg-[#0b0b0b] border border-[#1a1a1a] overflow-auto max-h-72">
                      <table className="min-w-full text-xs">
                        <thead className="bg-[#111] text-[#777] uppercase tracking-wide">
                          <tr>
                            <th className="text-left px-3 py-2">Track</th>
                            <th className="text-left px-3 py-2">Artist</th>
                            <th className="text-left px-3 py-2">Album</th>
                            <th className="text-left px-3 py-2">Owned</th>
                          </tr>
                        </thead>
                        <tbody>
                          {itemPreview.map((item, idx) => (
                            <tr key={`${idx}-${String(item.track_id ?? item.spotify_track_id ?? item.track_name ?? '')}`} className="border-t border-[#1a1a1a]">
                              <td className="px-3 py-2 text-text-primary">{toStringValue(item.track_name)}</td>
                              <td className="px-3 py-2 text-[#aaa]">{toStringValue(item.artist_name)}</td>
                              <td className="px-3 py-2 text-[#aaa]">{toStringValue(item.album_name)}</td>
                              <td className="px-3 py-2 text-[#aaa]">{toBool(item.is_owned) ? 'Yes' : 'No'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : selectedBuild.source === 'new_music' ? (
                    <div className="bg-[#0b0b0b] border border-[#1a1a1a] overflow-auto max-h-72">
                      <table className="min-w-full text-xs">
                        <thead className="bg-[#111] text-[#777] uppercase tracking-wide">
                          <tr>
                            <th className="text-left px-3 py-2">Artist</th>
                            <th className="text-left px-3 py-2">Release</th>
                            <th className="text-left px-3 py-2">Type</th>
                            <th className="text-left px-3 py-2">Date</th>
                            <th className="text-left px-3 py-2">In Library</th>
                          </tr>
                        </thead>
                        <tbody>
                          {itemPreview.map((item, idx) => (
                            <tr key={`${idx}-${String(item.id ?? item.title ?? '')}`} className="border-t border-[#1a1a1a]">
                              <td className="px-3 py-2 text-[#aaa]">{toStringValue(item.artist_name)}</td>
                              <td className="px-3 py-2 text-text-primary">{toStringValue(item.title)}</td>
                              <td className="px-3 py-2 text-[#aaa]">{toStringValue(item.record_type)}</td>
                              <td className="px-3 py-2 text-[#aaa]">{toStringValue(item.release_date)}</td>
                              <td className="px-3 py-2 text-[#aaa]">{toBool(item.in_library) ? 'Yes' : 'No'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : selectedBuild.source === 'custom_discovery' ? (
                    <div className="bg-[#0b0b0b] border border-[#1a1a1a] overflow-auto max-h-72">
                      <table className="min-w-full text-xs">
                        <thead className="bg-[#111] text-[#777] uppercase tracking-wide">
                          <tr>
                            <th className="text-left px-3 py-2">Artist</th>
                            <th className="text-left px-3 py-2">Reason</th>
                            <th className="text-left px-3 py-2">Similarity</th>
                          </tr>
                        </thead>
                        <tbody>
                          {itemPreview.map((item, idx) => (
                            <tr key={`${idx}-${String(item.artist ?? '')}`} className="border-t border-[#1a1a1a]">
                              <td className="px-3 py-2 text-text-primary">{toStringValue(item.artist)}</td>
                              <td className="px-3 py-2 text-[#aaa]">{toStringValue(item.reason)}</td>
                              <td className="px-3 py-2 text-[#aaa]">{toStringValue(item.similarity)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="bg-[#0b0b0b] border border-[#1a1a1a] p-3 max-h-72 overflow-auto space-y-2">
                      {itemPreview.map((item, idx) => (
                        <p key={idx} className="text-[12px] text-[#aaa] break-all">{JSON.stringify(item)}</p>
                      ))}
                    </div>
                  )}
                  {selectedBuild.item_count > itemPreview.length && (
                    <p className="text-[#444] text-[11px] mt-2">Showing first {itemPreview.length} items.</p>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {itemsModalBuild && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/70" onClick={closeItemsModal} />
          <div className="relative w-full max-w-6xl max-h-[90vh] bg-[#101010] border border-[#262626] flex flex-col">
            <div className="flex items-center justify-between p-4 border-b border-[#1f1f1f]">
              <div>
                <p className="text-text-primary text-sm font-semibold">{itemsModalBuild.name}</p>
                <p className="text-[#666] text-xs mt-1">
                  {itemDraft.length} item{itemDraft.length === 1 ? '' : 's'} in build
                </p>
              </div>
              <button
                onClick={closeItemsModal}
                className="text-[#888] hover:text-text-primary transition-colors"
                aria-label="Close item editor"
              >
                <X size={16} />
              </button>
            </div>

            <div className="p-4 overflow-auto">
              {itemDraft.length === 0 ? (
                <div className="text-[12px] text-[#666] border border-[#1f1f1f] bg-[#0b0b0b] p-3">No items in this build.</div>
              ) : itemsModalBuild.source === 'sync' ? (
                <table className="min-w-full text-xs bg-[#0b0b0b] border border-[#1a1a1a]">
                  <thead className="bg-[#111] text-[#777] uppercase tracking-wide">
                    <tr>
                      <th className="text-left px-3 py-2">Track</th>
                      <th className="text-left px-3 py-2">Artist</th>
                      <th className="text-left px-3 py-2">Album</th>
                      <th className="text-left px-3 py-2">Owned</th>
                      <th className="text-left px-3 py-2">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {itemDraft.map((item, idx) => (
                      <tr key={`${idx}-${String(item.track_id ?? item.spotify_track_id ?? item.track_name ?? '')}`} className="border-t border-[#1a1a1a]">
                        <td className="px-3 py-2 text-text-primary">{toStringValue(item.track_name)}</td>
                        <td className="px-3 py-2 text-[#aaa]">{toStringValue(item.artist_name)}</td>
                        <td className="px-3 py-2 text-[#aaa]">{toStringValue(item.album_name)}</td>
                        <td className="px-3 py-2 text-[#aaa]">{toBool(item.is_owned) ? 'Yes' : 'No'}</td>
                        <td className="px-3 py-2">
                          <button onClick={() => removeDraftItem(idx)} className="text-danger hover:text-danger/80 text-[11px]">
                            Remove
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : itemsModalBuild.source === 'new_music' ? (
                <table className="min-w-full text-xs bg-[#0b0b0b] border border-[#1a1a1a]">
                  <thead className="bg-[#111] text-[#777] uppercase tracking-wide">
                    <tr>
                      <th className="text-left px-3 py-2">Artist</th>
                      <th className="text-left px-3 py-2">Release</th>
                      <th className="text-left px-3 py-2">Type</th>
                      <th className="text-left px-3 py-2">Date</th>
                      <th className="text-left px-3 py-2">In Library</th>
                      <th className="text-left px-3 py-2">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {itemDraft.map((item, idx) => (
                      <tr key={`${idx}-${String(item.id ?? item.title ?? '')}`} className="border-t border-[#1a1a1a]">
                        <td className="px-3 py-2 text-[#aaa]">{toStringValue(item.artist_name)}</td>
                        <td className="px-3 py-2 text-text-primary">{toStringValue(item.title)}</td>
                        <td className="px-3 py-2 text-[#aaa]">{toStringValue(item.record_type)}</td>
                        <td className="px-3 py-2 text-[#aaa]">{toStringValue(item.release_date)}</td>
                        <td className="px-3 py-2 text-[#aaa]">{toBool(item.in_library) ? 'Yes' : 'No'}</td>
                        <td className="px-3 py-2">
                          <button onClick={() => removeDraftItem(idx)} className="text-danger hover:text-danger/80 text-[11px]">
                            Remove
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : itemsModalBuild.source === 'custom_discovery' ? (
                <table className="min-w-full text-xs bg-[#0b0b0b] border border-[#1a1a1a]">
                  <thead className="bg-[#111] text-[#777] uppercase tracking-wide">
                    <tr>
                      <th className="text-left px-3 py-2">Artist</th>
                      <th className="text-left px-3 py-2">Reason</th>
                      <th className="text-left px-3 py-2">Similarity</th>
                      <th className="text-left px-3 py-2">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {itemDraft.map((item, idx) => (
                      <tr key={`${idx}-${String(item.artist ?? '')}`} className="border-t border-[#1a1a1a]">
                        <td className="px-3 py-2 text-text-primary">{toStringValue(item.artist)}</td>
                        <td className="px-3 py-2 text-[#aaa]">{toStringValue(item.reason)}</td>
                        <td className="px-3 py-2 text-[#aaa]">{toStringValue(item.similarity)}</td>
                        <td className="px-3 py-2">
                          <button onClick={() => removeDraftItem(idx)} className="text-danger hover:text-danger/80 text-[11px]">
                            Remove
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="space-y-2">
                  {itemDraft.map((item, idx) => (
                    <div key={idx} className="bg-[#0b0b0b] border border-[#1a1a1a] p-3 flex items-start justify-between gap-3">
                      <p className="text-[12px] text-[#aaa] break-all">{JSON.stringify(item)}</p>
                      <button onClick={() => removeDraftItem(idx)} className="text-danger hover:text-danger/80 text-[11px]">
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="flex items-center justify-between gap-2 p-4 border-t border-[#1f1f1f]">
              <p className="text-[11px] text-[#666]">
                Remove any items you do not want in this build, then save.
              </p>
              <div className="flex items-center gap-2">
                <button onClick={closeItemsModal} className="btn-secondary text-xs px-3 py-1.5">
                  Close
                </button>
                <button
                  onClick={saveItemsDraft}
                  disabled={!itemsDirty || savingItemsBuildId === itemsModalBuild.id}
                  className="btn-primary text-xs px-3 py-1.5 disabled:opacity-40 inline-flex items-center gap-2"
                >
                  {savingItemsBuildId === itemsModalBuild.id ? <Loader2 size={12} className="animate-spin" /> : null}
                  Save Items
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

