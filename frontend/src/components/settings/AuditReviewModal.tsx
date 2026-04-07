import { useState, useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import { Link } from '@tanstack/react-router';
import { libraryBrowseApi, settingsApi } from '../../services/api';
import type { AuditItem, AuditCandidateItem } from '../../types';

// ---------------------------------------------------------------------------
// AuditReviewModal
// ---------------------------------------------------------------------------

interface AuditReviewModalProps {
  open: boolean;
  onClose: () => void;
  onRefreshAuditTotal: () => void;
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function AuditReviewModal({ open, onClose, onRefreshAuditTotal, toast }: AuditReviewModalProps) {
  const [auditReviewLoading, setAuditReviewLoading] = useState(false);
  const [auditReviewError, setAuditReviewError] = useState<string | null>(null);
  const [auditReviewItems, setAuditReviewItems] = useState<AuditItem[]>([]);
  const [auditInlineAlbumId, setAuditInlineAlbumId] = useState<string | null>(null);
  const [auditInlineLoading, setAuditInlineLoading] = useState(false);
  const [auditInlineError, setAuditInlineError] = useState<string | null>(null);
  const [auditInlineSaving, setAuditInlineSaving] = useState(false);
  const [auditInlineCandidates, setAuditInlineCandidates] = useState<Record<string, AuditCandidateItem[]>>({});
  const [primarySource, setPrimarySource] = useState<'itunes' | 'deezer'>('deezer');

  useEffect(() => {
    settingsApi.get().then(s => {
      setPrimarySource(s.catalog_primary === 'itunes' ? 'itunes' : 'deezer');
    }).catch(() => {});
  }, []);

  const loadAuditReviewItems = async (): Promise<AuditItem[]> => {
    setAuditReviewLoading(true);
    setAuditReviewError(null);
    try {
      const perPage = 200;
      let page = 1;
      let total = 0;
      let allItems: AuditItem[] = [];
      while (page <= 20) {
        const res = await libraryBrowseApi.getAudit({ page, per_page: perPage });
        if (page === 1) total = res.total ?? 0;
        allItems = allItems.concat(res.items ?? []);
        if ((res.items?.length ?? 0) < perPage || allItems.length >= total) break;
        page += 1;
      }
      allItems.sort((a, b) => (Number(b.match_confidence ?? 0) - Number(a.match_confidence ?? 0)));
      setAuditReviewItems(allItems);
      return allItems;
    } catch (err) {
      setAuditReviewError(err instanceof Error ? err.message : 'Failed to load review items');
      setAuditReviewItems([]);
      return [];
    } finally {
      setAuditReviewLoading(false);
    }
  };

  const loadInlineCandidates = async (albumId: string) => {
    setAuditInlineLoading(true);
    setAuditInlineError(null);
    try {
      const res = await libraryBrowseApi.getAuditCandidates({
        album_id: albumId,
        source: primarySource,
        limit: 5,
      });
      setAuditInlineCandidates(prev => ({ ...prev, [albumId]: res.candidates ?? [] }));
    } catch (err) {
      setAuditInlineError(err instanceof Error ? err.message : 'Failed to load candidates');
    } finally {
      setAuditInlineLoading(false);
    }
  };

  const toggleInlineReview = (albumId: string) => {
    if (auditInlineAlbumId === albumId) {
      setAuditInlineAlbumId(null);
      setAuditInlineError(null);
      return;
    }
    setAuditInlineAlbumId(albumId);
    setAuditInlineError(null);
    if (!auditInlineCandidates[albumId]) {
      void loadInlineCandidates(albumId);
    }
  };

  const refreshAuditAfterAction = async (albumId: string) => {
    const items = await loadAuditReviewItems();
    onRefreshAuditTotal();
    const stillExists = items.some(i => i.album_id === albumId);
    if (!stillExists) {
      setAuditInlineAlbumId(null);
      return;
    }
    await loadInlineCandidates(albumId);
  };

  const inlineConfirmCandidate = async (item: AuditItem, candidateId: string) => {
    setAuditInlineSaving(true);
    setAuditInlineError(null);
    try {
      await libraryBrowseApi.confirmAuditItem({
        entity_type: 'album',
        entity_id: item.album_id,
        source: primarySource,
        confirmed_id: candidateId,
      });
      toast.success(`${primarySource === 'itunes' ? 'iTunes' : 'Deezer'} match confirmed`);
      await refreshAuditAfterAction(item.album_id);
    } catch (err) {
      setAuditInlineError(err instanceof Error ? err.message : 'Failed to confirm candidate');
    } finally {
      setAuditInlineSaving(false);
    }
  };

  const inlineRejectSource = async (item: AuditItem) => {
    setAuditInlineSaving(true);
    setAuditInlineError(null);
    try {
      await libraryBrowseApi.rejectAuditItem({
        entity_type: 'album',
        entity_id: item.album_id,
        source: primarySource,
      });
      toast.success(`${primarySource === 'itunes' ? 'iTunes' : 'Deezer'} match rejected`);
      await refreshAuditAfterAction(item.album_id);
    } catch (err) {
      setAuditInlineError(err instanceof Error ? err.message : 'Failed to reject source');
    } finally {
      setAuditInlineSaving(false);
    }
  };

  // Load items when modal opens
  useEffect(() => {
    if (open) {
      setAuditInlineAlbumId(null);
      setAuditInlineError(null);
      void loadAuditReviewItems();
    }
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
      <div className="w-full max-w-5xl max-h-[85vh] bg-base border border-border-input flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
          <div>
            <h3 className="text-sm font-semibold text-text-primary">Review Low-Confidence Matches</h3>
            <p className="text-[11px] text-text-muted mt-0.5">
              Review inline here, or open any album to use the full Fix Match view.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => void loadAuditReviewItems()}
              disabled={auditReviewLoading}
              className="btn-secondary text-xs disabled:opacity-40"
            >
              {auditReviewLoading ? 'Loading...' : 'Refresh'}
            </button>
            <button
              onClick={() => {
                setAuditInlineAlbumId(null);
                setAuditInlineError(null);
                onClose();
              }}
              className="btn-secondary text-xs"
            >
              Close
            </button>
          </div>
        </div>

        {auditReviewError && (
          <div className="px-4 py-2 text-xs text-danger border-b border-border-subtle">
            {auditReviewError}
          </div>
        )}

        <div className="flex-1 overflow-y-auto">
          <div className="grid grid-cols-[1fr_1.2fr_70px_90px_170px] gap-3 px-4 py-2 border-b border-border-subtle sticky top-0 bg-base z-10">
            <span className="font-mono text-[10px] text-text-muted uppercase tracking-wider">Artist</span>
            <span className="font-mono text-[10px] text-text-muted uppercase tracking-wider">Album</span>
            <span className="font-mono text-[10px] text-text-muted uppercase tracking-wider">Year</span>
            <span className="font-mono text-[10px] text-text-muted uppercase tracking-wider">Confidence</span>
            <span className="font-mono text-[10px] text-text-muted uppercase tracking-wider">IDs</span>
            <span className="font-mono text-[10px] text-text-muted uppercase tracking-wider">Action</span>
          </div>

          {auditReviewLoading && (
            <div className="px-4 py-10 flex items-center justify-center text-text-muted text-xs">
              <Loader2 size={14} className="animate-spin mr-2" />
              Loading review queue...
            </div>
          )}

          {!auditReviewLoading && auditReviewItems.length === 0 && (
            <div className="px-4 py-10 text-center text-xs text-text-muted">
              No review items currently flagged.
            </div>
          )}

          {!auditReviewLoading && auditReviewItems.map(item => {
            const ids = `${item.itunes_album_id ? 'iT' : ''}${item.itunes_album_id && item.deezer_id ? ' + ' : ''}${item.deezer_id ? 'DZ' : ''}` || 'none';
            const isOpen = auditInlineAlbumId === item.album_id;
            const candidates = auditInlineCandidates[item.album_id] ?? [];
            const manualOverrides = item.manual_overrides ?? {};
            const lockedSources = Object.entries(manualOverrides).filter(([, v]) => Boolean(v?.locked));
            const hasManualLock = lockedSources.length > 0;
            const manualSummary = lockedSources.map(([k, v]) => `${k}:${v.state}`).join(', ');
            return (
              <div key={`${item.album_id}:${item.artist_id}`} className="border-b border-border-subtle">
                <div className="grid grid-cols-[1fr_1.2fr_70px_90px_170px] gap-3 px-4 py-2 items-center">
                  <span className="text-xs text-text-secondary truncate">{item.artist_name}</span>
                  <span className="text-xs text-text-primary truncate">{item.album_title}</span>
                  <span className="text-xs font-mono text-text-muted">{item.album_year ?? '-'}</span>
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="text-xs font-mono text-amber-400">{Math.round(item.match_confidence ?? 0)}%</span>
                    {hasManualLock && (
                      <span
                        className="text-[9px] font-mono text-green-400 border border-green-400/30 px-1 py-0.5 truncate"
                        title={`Manual: ${manualSummary}`}
                      >
                        manual
                      </span>
                    )}
                  </div>
                  <span className="text-[11px] font-mono text-text-muted">{ids}</span>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => toggleInlineReview(item.album_id)}
                      className="inline-flex btn-secondary text-xs"
                    >
                      {isOpen ? 'Hide' : 'Inline'}
                    </button>
                    <Link
                      to="/library/album/$id"
                      params={{ id: item.album_id }}
                      onClick={onClose}
                      className="inline-flex btn-secondary text-xs"
                    >
                      Open
                    </Link>
                  </div>
                </div>
                {isOpen && (
                  <div className="px-4 pb-3">
                    {auditInlineError && (
                      <p className="text-xs text-danger mb-2">{auditInlineError}</p>
                    )}
                    {auditInlineLoading ? (
                      <div className="flex items-center text-xs text-text-muted py-3">
                        <Loader2 size={12} className="animate-spin mr-2" />
                        Loading candidates...
                      </div>
                    ) : (
                      <div className="border border-border-subtle bg-base p-2">
                        <div className="flex items-center justify-between gap-2 mb-2">
                          <div className="flex items-center gap-1.5 min-w-0">
                            <span className="text-[11px] font-mono text-text-secondary uppercase tracking-wider">
                              {primarySource === 'itunes' ? 'iTunes' : 'Deezer'} (primary)
                            </span>
                            {item.manual_overrides?.[primarySource]?.locked && (
                              <span className="text-[9px] font-mono text-green-400 border border-green-400/30 px-1 py-0.5">
                                {item.manual_overrides[primarySource].state}
                              </span>
                            )}
                          </div>
                          <button
                            onClick={() => void inlineRejectSource(item)}
                            disabled={auditInlineSaving}
                            className="btn-secondary text-[10px] disabled:opacity-40"
                          >
                            Reject current
                          </button>
                        </div>
                        <p className="text-[10px] font-mono text-text-muted mb-2 truncate">
                          current: {primarySource === 'itunes' ? (item.itunes_album_id || '(none)') : (item.deezer_id || '(none)')}
                        </p>
                        <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
                          {candidates.length === 0 && (
                            <p className="text-xs text-text-muted">No candidates</p>
                          )}
                          {candidates.map(c => {
                            const currentId = primarySource === 'itunes' ? item.itunes_album_id : item.deezer_id;
                            const isCurrent = !!currentId && String(currentId) === String(c.candidate_id);
                            return (
                              <div key={`${primarySource}:${c.candidate_id}`} className="border border-border bg-surface px-2 py-1.5">
                                <div className="flex items-start justify-between gap-2">
                                  <div className="min-w-0">
                                    <p className="text-xs text-text-primary truncate">{c.candidate_title}</p>
                                    <p className="text-[10px] font-mono text-text-muted">
                                      {Math.round((c.candidate_score ?? 0) * 100)}% • tracks {(c.track_count && c.track_count > 0) ? c.track_count : '?'}
                                    </p>
                                  </div>
                                  <div className="flex items-center gap-1">
                                    {isCurrent && (
                                      <span className="text-[9px] font-mono text-green-400 border border-green-400/30 px-1 py-0.5">
                                        current
                                      </span>
                                    )}
                                    <button
                                      onClick={() => void inlineConfirmCandidate(item, c.candidate_id)}
                                      disabled={auditInlineSaving}
                                      className="px-1.5 py-1 text-[10px] bg-accent text-black hover:bg-accent/80 transition-colors disabled:opacity-40"
                                    >
                                      Confirm
                                    </button>
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
