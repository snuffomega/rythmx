import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, RefreshCw } from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { useApi } from '../hooks/useApi';
import { forgeFetchApi } from '../services/api';
import { useForgePipelineStore } from '../stores/useForgePipelineStore';
import { Spinner, EmptyState, ApiErrorBanner } from '../components/common';
import type { FetchQueueItem, FetchRun, FetchTask } from '../types';

interface ActivityPageProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

const RUN_STATUS_FILTERS = [
  { value: 'all', label: 'All Runs' },
  { value: 'running', label: 'Running' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
];

const STAGE_FILTERS = [
  { value: 'all', label: 'All Stages' },
  { value: 'queued', label: 'Queued' },
  { value: 'submitted', label: 'Submitted' },
  { value: 'downloading', label: 'Downloading' },
  { value: 'downloaded', label: 'Downloaded' },
  { value: 'tagged', label: 'Tagged' },
  { value: 'moved', label: 'Moved' },
  { value: 'scan_requested', label: 'Scan Requested' },
  { value: 'in_library', label: 'In Library' },
  { value: 'failed', label: 'Failed' },
  { value: 'unresolved', label: 'Unresolved' },
];

const SOURCE_FILTERS = [
  { value: 'all', label: 'All Sources' },
  { value: 'new_music', label: 'New Music' },
  { value: 'custom_discovery', label: 'Custom Discovery' },
  { value: 'sync', label: 'Sync' },
  { value: 'manual', label: 'Manual' },
];

const QUEUE_STATUS_FILTERS = [
  { value: 'active', label: 'Active Queue' },
  { value: 'all', label: 'All Queue Items' },
  { value: 'pending', label: 'Pending' },
  { value: 'running', label: 'Running' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'canceled', label: 'Canceled' },
];

const HOT_STAGES = ['queued', 'submitted', 'downloading', 'downloaded', 'tagged', 'moved'];

function stageClass(stage: string): string {
  switch (stage) {
    case 'in_library':
      return 'text-success border-success/40';
    case 'failed':
    case 'unresolved':
      return 'text-danger border-danger/40';
    case 'scan_requested':
    case 'moved':
    case 'tagged':
    case 'downloaded':
      return 'text-accent border-accent/40';
    default:
      return 'text-text-soft border-border-input';
  }
}

function runStatusClass(status: string): string {
  switch (status) {
    case 'completed':
      return 'text-success border-success/40';
    case 'failed':
      return 'text-danger border-danger/40';
    default:
      return 'text-accent border-accent/40';
  }
}

function queueStatusClass(status: string): string {
  switch (status) {
    case 'completed':
      return 'text-success border-success/40';
    case 'failed':
      return 'text-danger border-danger/40';
    case 'canceled':
      return 'text-warning border-warning/40';
    case 'running':
      return 'text-accent border-accent/40';
    default:
      return 'text-text-soft border-border-input';
  }
}

export function ActivityPage({ toast }: ActivityPageProps) {
  const navigate = useNavigate();
  const wsFetch = useForgePipelineStore(s => s.pipelines.fetch);

  const [runStatusFilter, setRunStatusFilter] = useState('all');
  const [taskStageFilter, setTaskStageFilter] = useState('all');
  const [providerFilter, setProviderFilter] = useState('all');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [queueStatusFilter, setQueueStatusFilter] = useState('active');
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [retryingRunId, setRetryingRunId] = useState<string | null>(null);
  const [retryingTaskId, setRetryingTaskId] = useState<number | null>(null);
  const [cancelingQueueId, setCancelingQueueId] = useState<string | null>(null);
  const [cancelingBatch, setCancelingBatch] = useState(false);
  const [selectedQueueIds, setSelectedQueueIds] = useState<Set<string>>(new Set());

  const runQueryKey = `${runStatusFilter}|${providerFilter}|${sourceFilter}`;
  const {
    data: runs,
    loading: runsLoading,
    error: runsError,
    refetch: refetchRuns,
  } = useApi(
    () =>
      forgeFetchApi.listRuns({
        status: runStatusFilter === 'all' ? undefined : runStatusFilter,
        provider: providerFilter === 'all' ? undefined : providerFilter,
        build_source: sourceFilter === 'all' ? undefined : sourceFilter,
        limit: 200,
      }),
    runQueryKey
  );

  const runsReady = runs !== null;

  const queueQueryKey = `${queueStatusFilter}|${sourceFilter}`;
  const {
    data: queueItems,
    loading: queueLoading,
    error: queueError,
    refetch: refetchQueue,
  } = useApi(
    () =>
      forgeFetchApi.listQueue({
        status:
          queueStatusFilter === 'pending' ||
          queueStatusFilter === 'running' ||
          queueStatusFilter === 'completed' ||
          queueStatusFilter === 'failed' ||
          queueStatusFilter === 'canceled'
            ? queueStatusFilter
            : undefined,
        include_canceled: queueStatusFilter === 'all' || queueStatusFilter === 'canceled',
        build_source: sourceFilter === 'all' ? undefined : sourceFilter,
        limit: 2000,
      }),
    queueQueryKey
  );
  const queueReady = queueItems !== null;

  useEffect(() => {
    if (!runs || runs.length === 0) {
      setSelectedRunId(null);
      return;
    }
    if (!selectedRunId || !runs.some(r => r.id === selectedRunId)) {
      setSelectedRunId(runs[0].id);
    }
  }, [runs, selectedRunId]);

  const taskQueryKey = `${selectedRunId || 'none'}|${taskStageFilter}|${providerFilter}`;
  const {
    data: tasks,
    loading: tasksLoading,
    error: tasksError,
    refetch: refetchTasks,
  } = useApi(
    () =>
      selectedRunId
        ? forgeFetchApi.getRunTasks(selectedRunId, {
            stage: taskStageFilter === 'all' ? undefined : taskStageFilter,
            provider: providerFilter === 'all' ? undefined : providerFilter,
            limit: 5000,
          })
        : Promise.resolve([] as FetchTask[]),
    taskQueryKey
  );

  const tasksReady = tasks !== null;

  const selectedRun = useMemo(
    () => (runs || []).find(r => r.id === selectedRunId) || null,
    [runs, selectedRunId]
  );

  const queueVisible = useMemo(() => {
    const all = queueItems || [];
    if (queueStatusFilter === 'all') return all;
    if (queueStatusFilter === 'active') {
      return all.filter(item => item.status === 'pending' || item.status === 'running');
    }
    return all.filter(item => item.status === queueStatusFilter);
  }, [queueItems, queueStatusFilter]);

  const queueCancelableIds = useMemo(
    () =>
      queueVisible
        .filter(item => item.status === 'pending' || item.status === 'running')
        .map(item => item.id),
    [queueVisible]
  );

  useEffect(() => {
    setSelectedQueueIds(prev => {
      if (prev.size === 0) return prev;
      const next = new Set<string>();
      for (const id of prev) {
        if (queueCancelableIds.includes(id)) next.add(id);
      }
      return next;
    });
  }, [queueCancelableIds]);

  const hasActiveRuns = useMemo(
    () => (runs || []).some(run => run.status === 'running' || run.active_tasks > 0),
    [runs]
  );

  const hasHotRuns = useMemo(
    () => (runs || []).some(run => HOT_STAGES.some(stage => (run.stage_counts?.[stage] || 0) > 0)),
    [runs]
  );

  const selectedRunIsHot = useMemo(
    () => !!selectedRun && HOT_STAGES.some(stage => (selectedRun.stage_counts?.[stage] || 0) > 0),
    [selectedRun]
  );

  const hasActiveQueue = useMemo(
    () => (queueItems || []).some(item => item.status === 'pending' || item.status === 'running'),
    [queueItems]
  );

  const shouldPoll = hasActiveRuns || hasActiveQueue || (selectedRun?.status === 'running');
  const pollIntervalMs = hasHotRuns || hasActiveQueue ? 10000 : 30000;

  const refreshAll = useCallback((includeTasks = true) => {
    refetchRuns();
    refetchQueue();
    if (includeTasks && selectedRunId) {
      refetchTasks();
    }
  }, [selectedRunId, refetchRuns, refetchTasks, refetchQueue]);

  useEffect(() => {
    if (!shouldPoll) return undefined;
    const id = window.setInterval(() => {
      refreshAll(selectedRunIsHot);
    }, pollIntervalMs);
    return () => window.clearInterval(id);
  }, [shouldPoll, selectedRunIsHot, pollIntervalMs, refreshAll]);

  const lastWsRefreshAt = useRef(0);
  useEffect(() => {
    if (!wsFetch.stage && !wsFetch.completedAt && !wsFetch.error) return;
    const now = Date.now();
    if (now - lastWsRefreshAt.current < 750) return;
    lastWsRefreshAt.current = now;
    refetchRuns();
    refetchQueue();
    if (selectedRunId && (!wsFetch.runId || wsFetch.runId === selectedRunId)) {
      refetchTasks();
    }
  }, [wsFetch.stage, wsFetch.completedAt, wsFetch.error, wsFetch.runId, selectedRunId, refetchRuns, refetchTasks, refetchQueue]);

  const providers = useMemo(() => {
    const set = new Set<string>();
    for (const run of runs || []) {
      if (run.provider) set.add(run.provider);
    }
    return Array.from(set).sort();
  }, [runs]);

  const handleRefresh = () => {
    refreshAll();
  };

  const handleRetryRun = async (run: FetchRun) => {
    setRetryingRunId(run.id);
    try {
      const result = await forgeFetchApi.retryRun(run.id);
      toast.success(`Retried ${result.retried} task(s)`);
      refreshAll(selectedRunId === run.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to retry run');
    } finally {
      setRetryingRunId(null);
    }
  };

  const handleRetryTask = async (task: FetchTask) => {
    if (!selectedRunId) return;
    setRetryingTaskId(task.id);
    try {
      const result = await forgeFetchApi.retryRun(selectedRunId, [task.id]);
      if (result.retried > 0) {
        toast.success(`Task retry queued`);
      } else {
        toast.error('Task was not eligible for retry');
      }
      refreshAll(true);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to retry task');
    } finally {
      setRetryingTaskId(null);
    }
  };

  const toggleQueueSelect = (queueId: string) => {
    setSelectedQueueIds(prev => {
      const next = new Set(prev);
      if (next.has(queueId)) {
        next.delete(queueId);
      } else {
        next.add(queueId);
      }
      return next;
    });
  };

  const handleQueueCancel = async (queueId: string) => {
    setCancelingQueueId(queueId);
    try {
      await forgeFetchApi.cancelQueueItem(queueId);
      toast.success('Queue item canceled');
      refreshAll(true);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to cancel queue item');
    } finally {
      setCancelingQueueId(null);
    }
  };

  const handleCancelSelected = async () => {
    const ids = Array.from(selectedQueueIds).filter(id => queueCancelableIds.includes(id));
    if (ids.length === 0) {
      toast.error('No cancelable queue items selected');
      return;
    }
    setCancelingBatch(true);
    try {
      const result = await forgeFetchApi.cancelQueueBatch({ queue_ids: ids });
      toast.success(`Canceled ${result.canceled} queue item(s)`);
      setSelectedQueueIds(new Set());
      refreshAll(true);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to cancel selected queue items');
    } finally {
      setCancelingBatch(false);
    }
  };

  const handleCancelFiltered = async () => {
    const ids = [...queueCancelableIds];
    if (ids.length === 0) {
      toast.error('No cancelable queue items in this filter');
      return;
    }
    setCancelingBatch(true);
    try {
      const result = await forgeFetchApi.cancelQueueBatch({ queue_ids: ids });
      toast.success(`Canceled ${result.canceled} queue item(s)`);
      setSelectedQueueIds(new Set());
      refreshAll(true);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to cancel filtered queue items');
    } finally {
      setCancelingBatch(false);
    }
  };

  return (
    <div className="py-8 space-y-8">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h1 className="page-title mb-1">Fetch Activity</h1>
          <p className="text-text-dim text-sm">
            Track fetch runs, task stages, failures, and library confirmation.
          </p>
        </div>
        <button onClick={handleRefresh} className="btn-secondary flex items-center gap-2 text-sm">
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-2">
        <select className="select text-xs" value={runStatusFilter} onChange={e => setRunStatusFilter(e.target.value)}>
          {RUN_STATUS_FILTERS.map(f => (
            <option key={f.value} value={f.value}>{f.label}</option>
          ))}
        </select>
        <select className="select text-xs" value={taskStageFilter} onChange={e => setTaskStageFilter(e.target.value)}>
          {STAGE_FILTERS.map(f => (
            <option key={f.value} value={f.value}>{f.label}</option>
          ))}
        </select>
        <select className="select text-xs" value={providerFilter} onChange={e => setProviderFilter(e.target.value)}>
          <option value="all">All Providers</option>
          {providers.map(p => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        <select className="select text-xs" value={sourceFilter} onChange={e => setSourceFilter(e.target.value)}>
          {SOURCE_FILTERS.map(f => (
            <option key={f.value} value={f.value}>{f.label}</option>
          ))}
        </select>
        <select className="select text-xs" value={queueStatusFilter} onChange={e => setQueueStatusFilter(e.target.value)}>
          {QUEUE_STATUS_FILTERS.map(f => (
            <option key={f.value} value={f.value}>{f.label}</option>
          ))}
        </select>
      </div>

      {runsError ? (
        <ApiErrorBanner error={runsError} onRetry={refetchRuns} />
      ) : runsLoading && !runsReady ? (
        <div className="py-10 flex items-center gap-2 text-text-muted text-sm">
          <Spinner size={16} />
          Loading fetch runs...
        </div>
      ) : !runs || runs.length === 0 ? (
        <EmptyState
          icon={<CheckCircle2 size={32} />}
          message="No fetch runs yet"
          sub="Run Build & Fetch from The Forge to populate this view."
        />
      ) : (
        <>
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            {runs.map(run => (
              <div
                key={run.id}
                role="button"
                tabIndex={0}
                onClick={() => setSelectedRunId(run.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setSelectedRunId(run.id);
                  }
                }}
                className={`w-full text-left border p-4 transition-colors ${
                  selectedRunId === run.id
                    ? 'border-accent bg-accent/5'
                    : 'border-border-subtle bg-base hover:border-border-input'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-text-primary text-sm font-semibold truncate">
                      {run.build_name || run.build_id}
                    </p>
                    <div className="flex items-center gap-2 mt-1 flex-wrap">
                      <span className={`text-[10px] border px-1.5 py-0.5 uppercase tracking-wide ${runStatusClass(run.status)}`}>
                        {run.status}
                      </span>
                      <span className="text-[10px] border border-border-input px-1.5 py-0.5 uppercase tracking-wide text-text-soft">
                        {run.provider}
                      </span>
                      {run.build_source && (
                        <span className="text-[10px] border border-border-input px-1.5 py-0.5 uppercase tracking-wide text-text-muted">
                          {run.build_source}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-text-primary text-xs tabular-nums">{run.processed_tasks}/{run.total_tasks}</p>
                    <p className="text-text-dim text-[11px]">processed</p>
                  </div>
                </div>
                <div className="grid grid-cols-4 gap-2 mt-3">
                  <div className="bg-surface-sunken border border-border-subtle p-2">
                    <p className="text-text-primary text-sm tabular-nums">{run.in_library}</p>
                    <p className="text-text-dim text-[10px] uppercase">In Library</p>
                  </div>
                  <div className="bg-surface-sunken border border-border-subtle p-2">
                    <p className="text-text-primary text-sm tabular-nums">{run.active_tasks}</p>
                    <p className="text-text-dim text-[10px] uppercase">Active</p>
                  </div>
                  <div className="bg-surface-sunken border border-border-subtle p-2">
                    <p className="text-danger text-sm tabular-nums">{run.failed}</p>
                    <p className="text-text-dim text-[10px] uppercase">Failed</p>
                  </div>
                  <div className="bg-surface-sunken border border-border-subtle p-2">
                    <p className="text-warning text-sm tabular-nums">{run.unresolved}</p>
                    <p className="text-text-dim text-[10px] uppercase">Unresolved</p>
                  </div>
                </div>
                <div className="flex items-center justify-between mt-3">
                  <p className="text-text-dim text-[11px] truncate">Run ID: {run.id}</p>
                  <div className="flex items-center gap-2">
                    <button
                      className="btn-secondary text-[11px] px-2 py-1"
                      onClick={(e) => {
                        e.stopPropagation();
                        navigate({ to: '/forge' });
                      }}
                    >
                      Open Build
                    </button>
                    <button
                      className="btn-secondary text-[11px] px-2 py-1"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRetryRun(run);
                      }}
                      disabled={retryingRunId === run.id}
                    >
                      {retryingRunId === run.id ? 'Retrying...' : 'Retry Run'}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="border border-border-subtle bg-base overflow-hidden">
            <div className="px-4 py-3 border-b border-border-subtle flex items-center justify-between gap-2">
              <p className="text-text-primary text-sm font-semibold">Fetch Queue</p>
              <div className="flex items-center gap-2">
                <button
                  className="btn-secondary text-[11px] px-2 py-1"
                  onClick={handleCancelSelected}
                  disabled={cancelingBatch || selectedQueueIds.size === 0}
                >
                  {cancelingBatch ? 'Canceling...' : 'Cancel Selected'}
                </button>
                <button
                  className="btn-secondary text-[11px] px-2 py-1"
                  onClick={handleCancelFiltered}
                  disabled={cancelingBatch || queueCancelableIds.length === 0}
                >
                  {cancelingBatch ? 'Canceling...' : 'Cancel Filtered'}
                </button>
              </div>
            </div>

            {queueError ? (
              <div className="p-4"><ApiErrorBanner error={queueError} onRetry={refetchQueue} /></div>
            ) : queueLoading && !queueReady ? (
              <div className="p-4 text-sm text-text-muted inline-flex items-center gap-2">
                <Spinner size={14} />
                Loading queue...
              </div>
            ) : queueVisible.length === 0 ? (
              <div className="p-6 text-sm text-text-muted">No queue items for this filter.</div>
            ) : (
              <div className="overflow-auto">
                <table className="min-w-full text-xs">
                  <thead className="bg-surface text-text-muted uppercase tracking-wide">
                    <tr>
                      <th className="text-left px-3 py-2">Select</th>
                      <th className="text-left px-3 py-2">Build</th>
                      <th className="text-left px-3 py-2">Source</th>
                      <th className="text-left px-3 py-2">Queue Status</th>
                      <th className="text-left px-3 py-2">Run</th>
                      <th className="text-left px-3 py-2">Updated</th>
                      <th className="text-left px-3 py-2">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {queueVisible.map((item: FetchQueueItem) => {
                      const selectable = item.status === 'pending' || item.status === 'running';
                      return (
                        <tr key={item.id} className="border-t border-border-subtle">
                          <td className="px-3 py-2">
                            <input
                              type="checkbox"
                              className="checkbox"
                              checked={selectedQueueIds.has(item.id)}
                              disabled={!selectable}
                              onChange={() => toggleQueueSelect(item.id)}
                            />
                          </td>
                          <td className="px-3 py-2 text-text-primary">{item.build_name || item.build_id}</td>
                          <td className="px-3 py-2 text-text-soft">{item.build_source || item.source}</td>
                          <td className="px-3 py-2">
                            <span className={`text-[10px] border px-1.5 py-0.5 uppercase tracking-wide ${queueStatusClass(item.status)}`}>
                              {item.status}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-text-soft">{item.run_id || '-'}</td>
                          <td className="px-3 py-2 text-text-soft">{item.updated_at}</td>
                          <td className="px-3 py-2">
                            {selectable ? (
                              <button
                                className="btn-secondary text-[11px] px-2 py-1"
                                onClick={() => handleQueueCancel(item.id)}
                                disabled={cancelingQueueId === item.id || cancelingBatch}
                              >
                                {cancelingQueueId === item.id ? 'Canceling...' : 'Cancel'}
                              </button>
                            ) : (
                              <span className="text-text-dim text-[11px]">-</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="border border-border-subtle bg-base overflow-hidden">
            <div className="px-4 py-3 border-b border-border-subtle flex items-center justify-between gap-2">
              <p className="text-text-primary text-sm font-semibold">
                Task Activity {selectedRun ? `(${selectedRun.build_name || selectedRun.build_id})` : ''}
              </p>
              {wsFetch.running && (
                <span className="text-[11px] text-accent">Live update: {wsFetch.stage || 'running'}</span>
              )}
            </div>

            {tasksError ? (
              <div className="p-4"><ApiErrorBanner error={tasksError} onRetry={refetchTasks} /></div>
            ) : tasksLoading && !tasksReady ? (
              <div className="p-4 text-sm text-text-muted inline-flex items-center gap-2">
                <Spinner size={14} />
                Loading tasks...
              </div>
            ) : !tasks || tasks.length === 0 ? (
              <div className="p-8">
                <EmptyState
                  icon={<AlertTriangle size={28} />}
                  message="No tasks for this run/filter"
                  sub="Change filters or start a new fetch run."
                />
              </div>
            ) : (
              <div className="overflow-auto">
                <table className="min-w-full text-xs">
                  <thead className="bg-surface text-text-muted uppercase tracking-wide">
                    <tr>
                      <th className="text-left px-3 py-2">Artist</th>
                      <th className="text-left px-3 py-2">Album</th>
                      <th className="text-left px-3 py-2">Provider</th>
                      <th className="text-left px-3 py-2">Stage</th>
                      <th className="text-left px-3 py-2">Last Update</th>
                      <th className="text-left px-3 py-2">Error</th>
                      <th className="text-left px-3 py-2">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tasks.map(task => (
                      <tr key={task.id} className="border-t border-border-subtle">
                        <td className="px-3 py-2 text-text-primary">{task.artist_name}</td>
                        <td className="px-3 py-2 text-text-primary">{task.album_name}</td>
                        <td className="px-3 py-2 text-text-soft">{task.provider}</td>
                        <td className="px-3 py-2">
                          <span className={`text-[10px] border px-1.5 py-0.5 uppercase tracking-wide ${stageClass(task.stage)}`}>
                            {task.stage}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-text-soft">{task.updated_at}</td>
                        <td className="px-3 py-2 text-text-soft max-w-[340px]">
                          {task.error_message || '-'}
                        </td>
                        <td className="px-3 py-2">
                          {(task.stage === 'failed' || task.stage === 'unresolved') ? (
                            <button
                              className="btn-secondary text-[11px] px-2 py-1"
                              onClick={() => handleRetryTask(task)}
                              disabled={retryingTaskId === task.id}
                            >
                              {retryingTaskId === task.id ? 'Retrying...' : 'Retry Task'}
                            </button>
                          ) : (
                            <span className="text-text-dim text-[11px]">-</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
