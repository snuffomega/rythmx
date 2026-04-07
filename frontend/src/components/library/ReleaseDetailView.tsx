import { useState, useEffect, useCallback } from 'react';
import { Loader2, Library as LibraryIcon, ChevronRight, Disc } from 'lucide-react';
import { Link } from '@tanstack/react-router';
import { libraryBrowseApi } from '../../services/api';
import { ApiErrorBanner } from '../common';
import { formatDuration } from './utils';
import { getImageUrl } from '../../utils/imageUrl';
import type {
  ReleaseDetail,
  ReleaseTrack,
  ReleaseSibling,
  UserReleasePrefs,
} from '../../types';

interface ReleaseDetailViewProps {
  releaseId: string;
}

export function ReleaseDetailView({ releaseId }: ReleaseDetailViewProps) {
  const [release, setRelease] = useState<ReleaseDetail | null>(null);
  const [tracks, setTracks] = useState<ReleaseTrack[]>([]);
  const [siblings, setSiblings] = useState<ReleaseSibling[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [prefs, setPrefs] = useState<UserReleasePrefs | null>(null);
  const [prefsLoading, setPrefsLoading] = useState(false);
  const [notes, setNotes] = useState('');

  useEffect(() => {
    setLoading(true);
    setError(null);
    libraryBrowseApi.getRelease(releaseId)
      .then(res => {
        setRelease(res.release);
        setTracks(res.tracks || []);
        setSiblings(res.siblings || []);
      })
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load release'))
      .finally(() => setLoading(false));
    libraryBrowseApi.getReleasePrefs(releaseId).then(res => {
      setPrefs(res.prefs);
      setNotes(res.prefs?.notes || '');
    });
  }, [releaseId]);

  const updatePref = useCallback((patch: { dismissed?: boolean; priority?: number; notes?: string }) => {
    setPrefsLoading(true);
    libraryBrowseApi.updateReleasePrefs(releaseId, patch)
      .then(() => libraryBrowseApi.getReleasePrefs(releaseId))
      .then(res => { setPrefs(res.prefs); if (res.prefs?.notes != null) setNotes(res.prefs.notes); })
      .finally(() => setPrefsLoading(false));
  }, [releaseId]);

  if (loading) {
    return <div className="flex-1 flex items-center justify-center"><Loader2 size={20} className="animate-spin text-text-muted" /></div>;
  }
  if (error || !release) {
    return <ApiErrorBanner error={error ?? 'Not found'} onRetry={() => setLoading(true)} />;
  }

  return (
    <div className="flex-1 overflow-y-auto custom-scrollbar">
      {/* Breadcrumb */}
      <div className="px-8 pt-4 pb-2 flex items-center gap-1.5">
        <Link to="/library" className="flex items-center gap-1 text-text-muted hover:text-text-primary text-xs font-mono uppercase tracking-wider transition-colors">
          <LibraryIcon size={13} /> Library
        </Link>
        {release && (
          <>
            <ChevronRight size={12} className="text-text-muted" />
            <Link to="/library/artist/$id" params={{ id: release.artist_id }}
                  className="text-text-muted hover:text-text-primary text-xs font-mono uppercase tracking-wider transition-colors truncate max-w-[200px]">
              {release.artist_name}
            </Link>
          </>
        )}
      </div>

      {/* Release header */}
      <div className="flex gap-6 px-8 py-5">
        <div className="w-48 h-48 flex-shrink-0 rounded-sm overflow-hidden bg-surface-raised border border-dashed border-border-strong flex items-center justify-center">
          {release.thumb_url ? (
            <img src={getImageUrl(release.thumb_url)} alt={release.title} className="w-full h-full object-cover" onError={(e) => { e.currentTarget.style.display = 'none'; }} />
          ) : (
            <Disc size={48} className="text-text-muted" />
          )}
        </div>
        <div className="flex flex-col justify-end">
          <p className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-1">
            {release.kind}{release.version_type && release.version_type !== 'original' && ` · ${release.version_type}`}
            <span className="ml-2 px-1.5 py-0.5 bg-border-strong rounded-sm text-[9px]">Missing</span>
          </p>
          <h1 className="text-2xl font-bold text-text-primary mb-1">{release.title}</h1>
          <p className="text-sm text-text-secondary font-mono">{release.artist_name}</p>
          <div className="flex gap-3 mt-2 text-[10px] font-mono text-text-muted">
            {release.release_date && <span>{release.release_date}</span>}
            {release.track_count != null && <span>{release.track_count} tracks</span>}
            {release.label && <span>{release.label}</span>}
            {release.genre_itunes && <span>{release.genre_itunes}</span>}
          </div>
          <div className="flex gap-2 mt-2">
            {release.catalog_source && (
              <span className="text-[9px] font-mono px-1.5 py-0.5 bg-border text-text-muted rounded-sm uppercase">{release.catalog_source}</span>
            )}
            {release.explicit === 1 && (
              <span className="text-[9px] font-mono px-1.5 py-0.5 bg-border-strong text-text-muted rounded-sm">E</span>
            )}
          </div>
          {/* Edition switcher */}
          {siblings.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-3">
              <span className="text-[10px] font-mono px-2 py-1 bg-accent/20 text-accent rounded-sm border border-accent/30">
                {release.version_type || 'original'}
              </span>
              {siblings.map(sib => (
                <Link
                  key={sib.id}
                  to="/library/release/$id"
                  params={{ id: sib.id }}
                  className={`text-[10px] font-mono px-2 py-1 rounded-sm border transition-colors ${
                    sib.is_owned
                      ? 'bg-green-500/10 text-green-400 border-green-500/30 hover:bg-green-500/20'
                      : 'bg-surface-raised text-text-muted border-border-strong hover:border-text-muted hover:text-text-secondary'
                  }`}
                >
                  {sib.version_type || 'original'}
                  {sib.is_owned ? ' ✓' : ''}
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* User controls */}
      <div className="px-8 py-4 border-t border-border-subtle flex flex-wrap items-center gap-4">
        <button
          onClick={() => updatePref({ dismissed: !prefs?.dismissed })}
          disabled={prefsLoading}
          className={`text-xs font-mono px-3 py-1.5 rounded-sm border transition-colors ${
            prefs?.dismissed
              ? 'border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20'
              : 'border-border-strong bg-surface-raised text-text-muted hover:text-text-secondary hover:border-border-strong'
          }`}
        >
          {prefs?.dismissed ? 'Dismissed — Undo' : 'Dismiss'}
        </button>
        <label className="flex items-center gap-2 text-xs font-mono text-text-muted">
          Priority
          <select
            value={prefs?.priority ?? 0}
            onChange={e => updatePref({ priority: Number(e.target.value) })}
            disabled={prefsLoading}
            className="bg-surface-raised border border-border-strong text-text-secondary text-xs font-mono px-2 py-1 rounded-sm"
          >
            <option value={0}>None</option>
            <option value={1}>Low</option>
            <option value={2}>Medium</option>
            <option value={3}>High</option>
          </select>
        </label>
        <div className="flex-1 min-w-[200px]">
          <input
            type="text"
            placeholder="Add a note..."
            value={notes}
            onChange={e => setNotes(e.target.value)}
            onBlur={() => { if (notes !== (prefs?.notes || '')) updatePref({ notes: notes || '' }); }}
            disabled={prefsLoading}
            className="w-full bg-surface-raised border border-border-strong text-text-secondary text-xs font-mono px-3 py-1.5 rounded-sm placeholder:text-text-muted/50 focus:outline-none focus:border-border-strong"
          />
        </div>
      </div>

      {/* Track listing */}
      {tracks.length > 0 && (
        <div className="px-8 py-4 border-t border-border-subtle">
          <h2 className="text-xs font-mono font-semibold text-text-muted uppercase tracking-widest mb-3">
            Track Listing
          </h2>
          <div className="space-y-0.5">
            {tracks.map((t, i) => (
              <div key={i} className="flex items-center gap-3 py-1.5 px-2 rounded-sm hover:bg-surface-raised transition-colors">
                <span className="text-text-muted font-mono text-xs w-6 text-right flex-shrink-0">{t.track_number}</span>
                <span className="text-text-primary text-sm flex-1 truncate">{t.title}</span>
                <span className="text-text-muted font-mono text-xs flex-shrink-0">{formatDuration(t.duration_ms)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {tracks.length === 0 && !loading && (
        <div className="px-8 py-4 border-t border-border-subtle">
          <p className="text-text-muted text-xs font-mono">No track listing available</p>
        </div>
      )}

      <div className="h-4" />
    </div>
  );
}
