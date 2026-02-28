import { useState, useEffect, useRef } from 'react';
import { Plus, RefreshCw, Loader2, Trash2, ChevronDown, ChevronUp, Upload, Download, X, ListMusic, Clock, CheckCircle, Minus, Pencil } from 'lucide-react';
import { useApi } from '../hooks/useApi';
import { playlistsApi } from '../services/api';
import { ConfirmDialog } from '../components/ConfirmDialog';
import type { PlaylistItem, PlaylistTrack, PlaylistSource } from '../types';

interface PlaylistsProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

const SOURCE_LABELS: Record<PlaylistSource, string> = {
  taste: 'taste',
  lastfm: 'lastfm',
  spotify: 'spotify',
  deezer: 'deezer',
  empty: 'empty',
  cc: 'Cruise Control',
};

const SOURCE_COLORS: Record<PlaylistSource, string> = {
  taste: 'text-accent',
  lastfm: 'text-danger',
  spotify: 'text-success',
  deezer: 'text-purple-400',
  empty: 'text-text-muted',
  cc: 'text-accent',
};

const MODE_LABELS: Record<string, string> = {
  cc_new_music: 'New Music',
  personal_discovery: 'Personal Discovery',
};

function OwnedBar({ owned, total }: { owned: number; total: number }) {
  const pct = total > 0 ? Math.round((owned / total) * 100) : 0;
  const color = pct > 20 ? 'bg-accent' : 'bg-danger';
  return (
    <div className="flex items-center gap-3">
      <span className="text-text-muted text-xs whitespace-nowrap">
        {owned} owned ({pct}%)
      </span>
      <div className="flex-1 h-0.5 bg-[#1e1e1e]">
        <div className={`h-full ${color} transition-all duration-300`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function AcqIcon({ status }: { status?: string | null }) {
  if (status === 'submitted') return <Loader2 size={10} className="text-accent animate-spin flex-shrink-0" aria-label="Downloading" />;
  if (status === 'found')     return <CheckCircle size={10} className="text-success flex-shrink-0" aria-label="Found" />;
  if (status === 'failed')    return <X size={10} className="text-danger flex-shrink-0" aria-label="Failed" />;
  if (status === 'skipped')   return <Minus size={10} className="text-text-muted flex-shrink-0" aria-label="Skipped" />;
  if (status === 'pending')   return <Clock size={10} className="text-text-muted flex-shrink-0" aria-label="Queued" />;
  // unowned, not in queue yet
  return <Clock size={10} className="text-[#2a2a2a] flex-shrink-0" aria-label="Not queued" />;
}

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

function PlaylistCard({
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
  const [renaming, setRenaming] = useState(false);
  const [renameVal, setRenameVal] = useState('');
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!expanded || tracks !== null) return;
    setTracksLoading(true);
    playlistsApi.getTracks(playlist.name)
      .then(t => setTracks(t as PlaylistTrack[]))
      .catch(() => setTracks([]))
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

  const handleTrackRemoved = () => {
    setTracks(null); // force track list reload (count in header stays until next full refresh)
  };

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
                <button
                  onClick={startRename}
                  className="group/name flex items-center gap-1.5 text-left"
                  aria-label="Rename playlist"
                >
                  <p className="text-text-primary font-semibold text-sm">{playlist.name}</p>
                  <Pencil size={10} className="text-accent flex-shrink-0" />
                </button>
              )}
              {source !== 'empty' && (
                <span className={`text-[10px] font-medium border border-current/30 px-1.5 py-0.5 ${SOURCE_COLORS[source as PlaylistSource] ?? 'text-text-muted'}`}>
                  {SOURCE_LABELS[source as PlaylistSource] ?? source}
                </span>
              )}
              {source === 'cc' && playlist.mode && MODE_LABELS[playlist.mode] && (
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
          <button
            onClick={() => onDelete(playlist.name)}
            className="text-[#2a2a2a] hover:text-danger transition-colors flex-shrink-0 p-1"
          >
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
            <button
              onClick={() => handle('rebuild')}
              disabled={!!busy}
              className="btn-secondary flex items-center gap-1.5 text-xs py-1.5 px-3"
            >
              {busy === 'rebuild' ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              Rebuild
            </button>
          )}
          {hasSync && (
            <button
              onClick={() => handle('sync')}
              disabled={!!busy}
              className="btn-secondary flex items-center gap-1.5 text-xs py-1.5 px-3"
            >
              {busy === 'sync' ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              Sync Now
            </button>
          )}
          <button
            onClick={() => handle('push')}
            disabled={!!busy}
            className="btn-primary flex items-center gap-1.5 text-xs py-1.5 px-3"
          >
            {busy === 'push' ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
            Push to Plex
          </button>
          <button
            onClick={() => handle('export')}
            disabled={!!busy}
            className="btn-secondary flex items-center gap-1.5 text-xs py-1.5 px-3"
          >
            {busy === 'export' ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
            Export M3U
          </button>
        </div>
      </div>
    </div>
  );
}

type NewPlaylistSource = Exclude<PlaylistSource, 'taste'>;

const SOURCE_OPTIONS: Array<{
  value: NewPlaylistSource;
  label: string;
  description: string;
}> = [
  { value: 'spotify', label: 'Import from Spotify', description: 'Match a public Spotify playlist against your library (requires Spotify credentials)' },
  { value: 'lastfm', label: 'Import from Last.fm playlist', description: 'Match a public Last.fm playlist against your library (uses your Last.fm API key)' },
  { value: 'deezer', label: 'Import from Deezer', description: 'Match a public Deezer playlist against your library (no credentials required)' },
  { value: 'empty', label: 'Empty playlist', description: 'Start blank — add tracks manually from Discovery' },
];

const URL_PLACEHOLDERS: Partial<Record<NewPlaylistSource, string>> = {
  spotify: 'https://open.spotify.com/playlist/...',
  lastfm: 'https://www.last.fm/user/.../library/playlists/...',
  deezer: 'https://www.deezer.com/playlist/...',
};

function NewPlaylistModal({ onClose, onCreate }: { onClose: () => void; onCreate: (data: Partial<PlaylistItem>) => Promise<void> }) {
  const [name, setName] = useState('My Playlist');
  const [source, setSource] = useState<NewPlaylistSource>('spotify');
  const [url, setUrl] = useState('');
  const [maxTracks, setMaxTracks] = useState(50);
  const [creating, setCreating] = useState(false);

  const handleSubmit = async () => {
    if (!name.trim()) return;
    setCreating(true);
    await onCreate({ name: name.trim(), source, source_url: url.trim() || undefined, max_tracks: maxTracks }).finally(() => setCreating(false));
  };

  return (
    <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4">
      <div className="bg-[#111] border border-[#222] w-full max-w-sm">
        <div className="flex items-center justify-between p-5 border-b border-[#1a1a1a]">
          <div className="flex items-center gap-2">
            <ListMusic size={16} className="text-accent" />
            <h2 className="text-text-primary font-semibold text-sm">New Playlist</h2>
          </div>
          <button onClick={onClose} className="text-text-muted hover:text-text-primary transition-colors">
            <X size={16} />
          </button>
        </div>

        <div className="p-5 space-y-5">
          <div>
            <label className="label">Playlist Name</label>
            <input
              className="input"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="My Playlist"
              autoFocus
            />
          </div>

          <div>
            <label className="label">Source</label>
            <div className="space-y-1.5 mt-1">
              {SOURCE_OPTIONS.map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setSource(opt.value)}
                  className={`w-full text-left p-3 border transition-colors ${
                    source === opt.value
                      ? 'border-accent/60 bg-accent/5'
                      : 'border-[#1e1e1e] bg-[#0d0d0d] hover:border-[#2a2a2a]'
                  }`}
                >
                  <div className="flex items-start gap-2.5">
                    <div className={`w-3.5 h-3.5 rounded-full border-2 flex-shrink-0 mt-0.5 ${source === opt.value ? 'border-accent bg-accent' : 'border-[#444]'}`} />
                    <div>
                      <p className="text-text-primary text-xs font-medium">{opt.label}</p>
                      <p className="text-[#444] text-[11px] mt-0.5">{opt.description}</p>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {URL_PLACEHOLDERS[source] && (
            <div>
              <label className="label">Playlist URL</label>
              <input
                className="input"
                value={url}
                onChange={e => setUrl(e.target.value)}
                placeholder={URL_PLACEHOLDERS[source]}
              />
            </div>
          )}

          <div className="flex items-center justify-between">
            <div>
              <p className="text-text-secondary text-xs font-medium">Max tracks</p>
              <p className="text-[#444] text-[11px]">How many tracks to include in the playlist</p>
            </div>
            <input
              type="number"
              className="input w-20 text-center"
              value={maxTracks}
              min={1}
              max={1000}
              onChange={e => setMaxTracks(Number(e.target.value))}
            />
          </div>

          <button
            onClick={handleSubmit}
            disabled={creating || !name.trim()}
            className="btn-primary w-full flex items-center justify-center gap-2"
          >
            {creating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
            Create Playlist
          </button>
        </div>
      </div>
    </div>
  );
}

export function Playlists({ toast }: PlaylistsProps) {
  const { data: playlists, loading, refetch } = useApi(() => playlistsApi.getAll());
  const [showModal, setShowModal] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const handleCreate = async (data: Partial<PlaylistItem>) => {
    try {
      await playlistsApi.create(data);
      toast.success(`Playlist "${data.name}" created`);
      setShowModal(false);
      refetch();
    } catch {
      toast.error('Failed to create playlist');
    }
  };

  const handleAction = async (name: string, action: 'rebuild' | 'sync' | 'push' | 'export') => {
    const apiMap: Record<string, (n: string) => Promise<unknown>> = {
      rebuild: playlistsApi.rebuild,
      sync: playlistsApi.sync,
      push: playlistsApi.publish,
      export: playlistsApi.export,
    };
    try {
      await apiMap[action](name);
      const labels: Record<string, string> = { rebuild: 'Rebuilt', sync: 'Sync started', push: 'Pushed to Plex', export: 'Exported' };
      toast.success(`${labels[action]}: "${name}"`);
      refetch();
    } catch {
      toast.error(`Failed to ${action} "${name}"`);
    }
  };

  const handleDelete = async () => {
    if (!confirmDelete) return;
    try {
      await playlistsApi.delete(confirmDelete);
      toast.success(`Deleted "${confirmDelete}"`);
      refetch();
    } catch {
      toast.error('Failed to delete playlist');
    }
    setConfirmDelete(null);
  };

  const handleRename = async (name: string, newName: string) => {
    try {
      await playlistsApi.rename(name, newName);
      toast.success(`Renamed to "${newName}"`);
      refetch();
    } catch {
      toast.error('Failed to rename playlist');
    }
  };

  return (
    <div className="py-8 space-y-6">
      <div>
        <div className="flex items-center justify-between mb-1">
          <h1 className="page-title">Playlist Manager</h1>
          <button
            onClick={() => setShowModal(true)}
            className="btn-primary flex items-center gap-2"
          >
            <Plus size={14} />
            New Playlist
          </button>
        </div>
        <p className="text-text-muted text-sm">Manage and publish your playlists</p>
        <div className="border-b border-[#1a1a1a] mt-4" />
      </div>

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="bg-[#0e0e0e] border border-[#1a1a1a] p-4 space-y-3">
              <div className="h-4 animate-pulse bg-[#141414] rounded-sm w-48" />
              <div className="h-3 animate-pulse bg-[#141414] rounded-sm w-24" />
              <div className="h-0.5 animate-pulse bg-[#141414] w-full" />
            </div>
          ))}
        </div>
      ) : !playlists || (playlists as PlaylistItem[]).length === 0 ? (
        <div className="text-center py-20">
          <ListMusic size={36} className="text-[#1e1e1e] mx-auto mb-3" />
          <p className="text-[#444] text-sm">No playlists yet</p>
          <p className="text-[#333] text-xs mt-1">Create one to get started</p>
        </div>
      ) : (
        <div className="space-y-3">
          {(playlists as PlaylistItem[]).map(p => (
            <PlaylistCard
              key={p.name}
              playlist={p}
              onAction={handleAction}
              onDelete={name => setConfirmDelete(name)}
              onRename={handleRename}
            />
          ))}
        </div>
      )}

      {showModal && (
        <NewPlaylistModal
          onClose={() => setShowModal(false)}
          onCreate={handleCreate}
        />
      )}

      <ConfirmDialog
        open={!!confirmDelete}
        title="Delete Playlist?"
        description={`"${confirmDelete}" will be permanently deleted.`}
        confirmLabel="Delete"
        danger
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  );
}
