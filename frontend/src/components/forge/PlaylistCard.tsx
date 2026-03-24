import { useState, useRef, useEffect } from 'react';
import { RefreshCw, Loader2, Trash2, ChevronDown, ChevronUp, Upload, Download, X, Pencil } from 'lucide-react';
import { playlistsApi } from '../../services/api';
import { OwnedBar } from './OwnedBar';
import { AcqIcon } from './AcqIcon';
import type { PlaylistItem, PlaylistTrack, PlaylistSource } from '../../types';

const SOURCE_LABELS: Record<PlaylistSource, string> = {
  taste: 'taste',
  lastfm: 'lastfm',
  spotify: 'spotify',
  deezer: 'deezer',
  empty: 'empty',
  new_music: 'New Music',
};

const SOURCE_COLORS: Record<PlaylistSource, string> = {
  taste: 'text-accent',
  lastfm: 'text-danger',
  spotify: 'text-success',
  deezer: 'text-purple-400',
  empty: 'text-text-muted',
  new_music: 'text-accent',
};

const MODE_LABELS: Record<string, string> = {
  new_music: 'New Music',
  personal_discovery: 'Personal Discovery',
};

function TrackRow({ track, playlistName, onRemoved }: {
  track: PlaylistTrack;
  playlistName: string;
  onRemoved: () => void;
}) {
  const [removing, setRemoving] = useState(false);

  const handleRemove = async () => {
    if (track.row_id == null) return;
    setRemoving(true);
    try {
      await playlistsApi.removeTrack(playlistName, track.row_id);
      onRemoved();
    } catch {
      setRemoving(false);
    }
  };

  return (
    <div className="group flex items-center gap-2 py-1.5 border-b border-[#111] last:border-0">
      <button
        onClick={handleRemove}
        disabled={removing}
        className="flex-shrink-0 w-3 h-3 flex items-center justify-center"
        aria-label="Remove track"
      >
        {removing && <Loader2 size={10} className="animate-spin text-text-muted" />}
        {!removing && (
          <span className="w-3 h-3 flex items-center justify-center">
            <span className={`group-hover:hidden w-1.5 h-1.5 rounded-full ${track.is_owned ? 'bg-accent' : 'bg-[#2a2a2a]'}`} />
            <X size={12} className="hidden group-hover:block text-[#555] hover:text-danger" />
          </span>
        )}
      </button>
      <span className={`text-xs truncate flex-1 ${track.is_owned ? 'text-text-primary' : 'text-[#3a3a3a] italic'}`}>
        {track.artist} — {track.name}
      </span>
      {!track.is_owned && <AcqIcon status={track.acquisition_status} />}
    </div>
  );
}

export function PlaylistCard({
  playlist,
  onAction,
  onDelete,
  onRename,
}: {
  playlist: PlaylistItem;
  onAction: (name: string, action: 'rebuild' | 'sync' | 'push' | 'export') => Promise<void>;
  onDelete: (name: string) => void;
  onRename: (name: string, newName: string) => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [tracks, setTracks] = useState<PlaylistTrack[] | null>(null);
  const [tracksLoading, setTracksLoading] = useState(false);
  const [tracksError, setTracksError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [renameVal, setRenameVal] = useState('');
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!expanded || tracks !== null) return;
    setTracksLoading(true);
    setTracksError(null);
    playlistsApi.getTracks(playlist.name)
      .then(t => setTracks(t as PlaylistTrack[]))
      .catch((err: unknown) => setTracksError(err instanceof Error ? err.message : 'Failed to load tracks'))
      .finally(() => setTracksLoading(false));
  }, [expanded, playlist.name, tracks]);

  useEffect(() => {
    if (renaming) renameInputRef.current?.focus();
  }, [renaming]);

  const handle = async (action: 'rebuild' | 'sync' | 'push' | 'export') => {
    setBusy(action);
    await onAction(playlist.name, action).finally(() => setBusy(null));
  };

  const startRename = () => {
    setRenameVal(playlist.name);
    setRenaming(true);
  };

  const submitRename = async () => {
    const trimmed = renameVal.trim();
    setRenaming(false);
    if (!trimmed || trimmed === playlist.name) return;
    await onRename(playlist.name, trimmed);
  };

  const handleTrackRemoved = () => setTracks(null);

  const owned = playlist.owned_count ?? 0;
  const total = playlist.track_count ?? 0;
  const source = playlist.source ?? 'empty';
  const hasSync = source === 'lastfm' || source === 'spotify' || source === 'deezer';
  const hasRebuild = source === 'taste';

  return (
    <div className="bg-[#0e0e0e] border border-[#1a1a1a] hover:border-[#252525] transition-colors">
      <div className="p-4 space-y-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap mb-1">
              {renaming ? (
                <input
                  ref={renameInputRef}
                  className="text-text-primary font-semibold text-sm bg-transparent border-b border-accent outline-none w-40"
                  value={renameVal}
                  onChange={e => setRenameVal(e.target.value)}
                  onBlur={submitRename}
                  onKeyDown={e => { if (e.key === 'Enter') { e.currentTarget.blur(); } else if (e.key === 'Escape') { setRenaming(false); } }}
                />
              ) : (
                <button onClick={startRename} className="group/name flex items-center gap-1.5 text-left" aria-label="Rename playlist">
                  <p className="text-text-primary font-semibold text-sm">{playlist.name}</p>
                  <Pencil size={10} className="text-accent flex-shrink-0" />
                </button>
              )}
              {source !== 'empty' && (
                <span className={`text-[10px] font-medium border border-current/30 px-1.5 py-0.5 ${SOURCE_COLORS[source as PlaylistSource] ?? 'text-text-muted'}`}>
                  {SOURCE_LABELS[source as PlaylistSource] ?? source}
                </span>
              )}
              {source === 'new_music' && playlist.mode && MODE_LABELS[playlist.mode] && (
                <span className="text-[10px] font-medium border border-current/30 px-1.5 py-0.5 text-white">
                  {MODE_LABELS[playlist.mode]}
                </span>
              )}
              {playlist.auto_sync && (
                <span className="text-[10px] text-text-muted border border-[#2a2a2a] px-1.5 py-0.5">auto-sync</span>
              )}
              {playlist.last_synced && (
                <span className="text-[#333] text-[10px]">Last synced {new Date(playlist.last_synced).toLocaleDateString()}</span>
              )}
            </div>
            <p className="text-[#444] text-xs">{total} tracks</p>
          </div>
          <button onClick={() => onDelete(playlist.name)} className="text-[#2a2a2a] hover:text-danger transition-colors flex-shrink-0 p-1">
            <Trash2 size={13} />
          </button>
        </div>

        {total > 0 && <OwnedBar owned={owned} total={total} />}

        {total > 0 && (
          <button
            onClick={() => setExpanded(v => !v)}
            className="flex items-center gap-1.5 text-[#444] hover:text-text-muted text-xs transition-colors"
          >
            {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            Track list ({total})
          </button>
        )}

        {expanded && (
          <div className="max-h-48 overflow-y-auto scrollbar-hide border-t border-[#111] pt-2">
            {tracksLoading ? (
              <div className="space-y-2 py-1">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="h-3 animate-pulse bg-[#141414] rounded-sm w-full" />
                ))}
              </div>
            ) : tracksError ? (
              <p className="text-danger text-xs py-2">{tracksError}</p>
            ) : tracks && tracks.length > 0 ? (
              (tracks as PlaylistTrack[]).map((t, i) => (
                <TrackRow key={t.row_id ?? i} track={t} playlistName={playlist.name} onRemoved={handleTrackRemoved} />
              ))
            ) : (
              <p className="text-[#333] text-xs py-2">No tracks</p>
            )}
          </div>
        )}

        <div className="flex items-center gap-2 flex-wrap pt-1">
          {hasRebuild && (
            <button onClick={() => handle('rebuild')} disabled={!!busy} className="btn-secondary flex items-center gap-1.5 text-xs py-1.5 px-3">
              {busy === 'rebuild' ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              Rebuild
            </button>
          )}
          {hasSync && (
            <button onClick={() => handle('sync')} disabled={!!busy} className="btn-secondary flex items-center gap-1.5 text-xs py-1.5 px-3">
              {busy === 'sync' ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              Sync Now
            </button>
          )}
          <button onClick={() => handle('push')} disabled={!!busy} className="btn-primary flex items-center gap-1.5 text-xs py-1.5 px-3">
            {busy === 'push' ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
            Push to Plex
          </button>
          <button onClick={() => handle('export')} disabled={!!busy} className="btn-secondary flex items-center gap-1.5 text-xs py-1.5 px-3">
            {busy === 'export' ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
            Export M3U
          </button>
        </div>
      </div>
    </div>
  );
}
