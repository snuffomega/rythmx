/**
 * LibraryPlaylists — Browse and manage platform playlists (Navidrome / Plex).
 *
 * Features:
 * - Card grid: cover art (music note fallback), name, track count, platform badge
 * - Click card → detail view: track list with per-track play button
 * - Play + Shuffle buttons → send playlist tracks to player queue
 * - Inline rename on playlist name
 * - Delete with confirmation
 * - Sync button → POST /library/playlists/sync
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Music,
  RefreshCw,
  Play,
  Shuffle,
  Trash2,
  Pencil,
  Check,
  X,
  ChevronLeft,
  Loader2,
  ListMusic,
  Clock,
} from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { libraryPlaylistsApi } from '../services/api';
import { usePlayerStore } from '../stores/usePlayerStore';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ApiErrorBanner } from '../components/common';
import { getImageUrl } from '../utils/imageUrl';
import type { LibPlaylist, LibPlaylistTrack } from '../types';

interface LibraryPlaylistsProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

// Format ms → m:ss
function fmtDuration(ms: number | null): string {
  if (!ms) return '0:00';
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

// Format total playlist duration in hours/minutes
function fmtPlaylistDuration(ms: number): string {
  const total = Math.floor(ms / 1000 / 60);
  if (total < 60) return `${total} min`;
  const h = Math.floor(total / 60);
  const m = total % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function PlatformBadge({ platform }: { platform: string }) {
  const label = platform === 'navidrome' ? 'Navidrome' : platform === 'plex' ? 'Plex' : platform;
  const color =
    platform === 'navidrome'
      ? 'bg-purple-500/20 text-purple-300'
      : platform === 'plex'
        ? 'bg-yellow-500/20 text-yellow-300'
        : 'bg-surface-overlay text-text-muted';
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${color}`}>
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Track row
// ---------------------------------------------------------------------------
function TrackRow({
  track,
  onPlay,
}: {
  track: LibPlaylistTrack;
  onPlay: () => void;
}) {
  return (
    <div className="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-raised group rounded-lg">
      <span className="text-text-muted text-xs w-5 text-right shrink-0">
        {track.position + 1}
      </span>
      <button
        onClick={onPlay}
        className="shrink-0 w-6 h-6 flex items-center justify-center rounded-full
                   bg-accent/0 group-hover:bg-accent/20 text-accent/0
                   group-hover:text-accent transition-colors"
        aria-label={`Play ${track.title}`}
      >
        <Play size={12} fill="currentColor" />
      </button>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-text-primary truncate">{track.title}</p>
        <p className="text-xs text-text-muted truncate">
          {track.artist_name ?? '—'}
          {track.album_title ? ` · ${track.album_title}` : ''}
        </p>
      </div>
      <span className="text-xs text-text-muted shrink-0">
        {fmtDuration(track.duration)}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail view
// ---------------------------------------------------------------------------
function PlaylistDetail({
  playlist,
  onBack,
  onDeleted,
  onRenamed,
  toast,
}: {
  playlist: LibPlaylist;
  onBack: () => void;
  onDeleted: (id: string) => void;
  onRenamed: (id: string, name: string) => void;
  toast: LibraryPlaylistsProps['toast'];
}) {
  const [tracks, setTracks] = useState<LibPlaylistTrack[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState(playlist.name);
  const [savingName, setSavingName] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const playQueue = usePlayerStore((s) => s.playQueue);

  const queueTracks = useMemo(
    () =>
      tracks.map((t) => ({
        id: t.track_id,
        title: t.title,
        artist: t.artist_name ?? '',
        album: t.album_title ?? '',
        duration: t.duration,
        thumb_url: playlist.cover_url ?? null,
        thumb_hash: null,
        source_platform: playlist.source_platform,
      })),
    [tracks, playlist.source_platform]
  );
  const hasPlayableTracks = queueTracks.length > 0;

  useEffect(() => {
    setLoading(true);
    setError(null);
    libraryPlaylistsApi
      .getTracks(playlist.id)
      .then(setTracks)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [playlist.id]);

  const handlePlayAll = useCallback(() => {
    if (!hasPlayableTracks) return;
    playQueue(queueTracks);
  }, [hasPlayableTracks, queueTracks, playQueue]);

  const handleShuffleAll = useCallback(() => {
    if (!hasPlayableTracks) return;
    const shuffled = [...queueTracks].sort(() => Math.random() - 0.5);
    playQueue(shuffled);
  }, [hasPlayableTracks, queueTracks, playQueue]);

  const handlePlayTrack = useCallback(
    (idx: number) => {
      if (idx < 0 || idx >= queueTracks.length) return;
      playQueue(queueTracks.slice(idx));
    },
    [queueTracks, playQueue]
  );

  const handleSaveName = useCallback(async () => {
    const trimmed = nameInput.trim();
    if (!trimmed || trimmed === playlist.name) {
      setEditingName(false);
      setNameInput(playlist.name);
      return;
    }
    setSavingName(true);
    try {
      await libraryPlaylistsApi.rename(playlist.id, trimmed);
      onRenamed(playlist.id, trimmed);
      toast.success('Playlist renamed');
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Rename failed');
      setNameInput(playlist.name);
    } finally {
      setSavingName(false);
      setEditingName(false);
    }
  }, [nameInput, playlist.id, playlist.name, onRenamed, toast]);

  const handleDelete = useCallback(async () => {
    setDeleting(true);
    try {
      await libraryPlaylistsApi.delete(playlist.id);
      onDeleted(playlist.id);
      toast.success('Playlist deleted');
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Delete failed');
      setDeleting(false);
      setConfirmDelete(false);
    }
  }, [playlist.id, onDeleted, toast]);

  const totalDuration = tracks.reduce((sum, t) => sum + (t.duration ?? 0), 0);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-start gap-4 px-6 py-5 border-b border-border-subtle">
        <button
          onClick={onBack}
          className="mt-0.5 shrink-0 text-text-muted hover:text-text-primary transition-colors"
          aria-label="Back to playlists"
        >
          <ChevronLeft size={20} />
        </button>

        {/* Cover placeholder */}
        <div className="w-16 h-16 rounded-lg bg-surface-raised flex items-center justify-center shrink-0">
          <ListMusic size={28} className="text-text-muted" />
        </div>

        <div className="flex-1 min-w-0">
          {editingName ? (
            <div className="flex items-center gap-2">
              <input
                className="bg-surface-raised border border-accent rounded px-2 py-1 text-sm text-text-primary focus:outline-none"
                value={nameInput}
                onChange={(e) => setNameInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleSaveName();
                  if (e.key === 'Escape') {
                    setEditingName(false);
                    setNameInput(playlist.name);
                  }
                }}
                autoFocus
                disabled={savingName}
              />
              <button
                onClick={handleSaveName}
                disabled={savingName}
                className="text-success hover:text-success/80 transition-colors"
                aria-label="Save name"
              >
                {savingName ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
              </button>
              <button
                onClick={() => { setEditingName(false); setNameInput(playlist.name); }}
                className="text-text-muted hover:text-text-primary transition-colors"
                aria-label="Cancel rename"
              >
                <X size={14} />
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold text-text-primary truncate">
                {nameInput}
              </h2>
              <button
                onClick={() => setEditingName(true)}
                className="text-text-muted hover:text-text-primary transition-colors"
                aria-label="Rename playlist"
              >
                <Pencil size={13} />
              </button>
            </div>
          )}
          <div className="flex items-center gap-2 mt-1">
            <PlatformBadge platform={playlist.source_platform} />
            <span className="text-xs text-text-muted">
              {tracks.length} tracks
              {totalDuration > 0 ? ` · ${fmtPlaylistDuration(totalDuration)}` : ''}
            </span>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={handlePlayAll}
            disabled={!hasPlayableTracks}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-accent hover:bg-accent/80
                       text-black text-xs font-medium rounded-lg transition-colors
                       disabled:opacity-40 disabled:pointer-events-none"
          >
            <Play size={12} fill="currentColor" />
            Play
          </button>
          <button
            onClick={handleShuffleAll}
            disabled={!hasPlayableTracks}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-border-strong hover:border-border-strong
                       text-text-secondary text-xs font-medium rounded-lg transition-colors
                       disabled:opacity-40 disabled:pointer-events-none"
          >
            <Shuffle size={12} />
            Shuffle
          </button>
          <button
            onClick={() => setConfirmDelete(true)}
            className="p-1.5 text-text-muted hover:text-danger transition-colors rounded"
            aria-label="Delete playlist"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {/* Track list */}
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {loading && (
          <div className="flex items-center justify-center py-12 text-text-muted">
            <Loader2 size={20} className="animate-spin mr-2" />
            Loading tracks…
          </div>
        )}
        {error && <ApiErrorBanner error={error} />}
        {!loading && !error && tracks.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 text-text-muted">
            <Music size={32} className="mb-2 opacity-30" />
            <p className="text-sm">This playlist is empty</p>
          </div>
        )}
        {!loading && tracks.map((t, idx) => (
          <TrackRow
            key={`${t.track_id}-${t.position}`}
            track={t}
            onPlay={() => handlePlayTrack(idx)}
          />
        ))}
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete playlist"
        description={`Delete "${nameInput}" from your platform? This cannot be undone.`}
        confirmLabel={deleting ? 'Deleting…' : 'Delete'}
        danger
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Playlist card
// ---------------------------------------------------------------------------
function PlaylistCard({
  playlist,
  onClick,
}: {
  playlist: LibPlaylist;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="flex flex-col bg-surface hover:bg-surface-highlight border border-border-subtle
                 hover:border-border-input rounded-xl p-4 text-left transition-colors
                 group focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      {/* Cover */}
      <div className="w-full aspect-square rounded-lg bg-surface-raised flex items-center justify-center mb-3 overflow-hidden">
        {playlist.cover_url ? (
          <img
            src={getImageUrl(playlist.cover_url)}
            alt={playlist.name}
            className="w-full h-full object-cover"
          />
        ) : (
          <ListMusic size={36} className="text-text-muted opacity-40 group-hover:opacity-60 transition-opacity" />
        )}
      </div>

      {/* Name */}
      <p className="text-sm font-medium text-text-primary truncate w-full">{playlist.name}</p>

      {/* Meta row */}
      <div className="flex items-center gap-2 mt-1 w-full">
        <span className="text-xs text-text-muted flex items-center gap-1 shrink-0">
          <Clock size={10} />
          {playlist.track_count} tracks
        </span>
        <div className="flex-1" />
        <PlatformBadge platform={playlist.source_platform} />
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------
export function LibraryPlaylists({ toast }: LibraryPlaylistsProps) {
  const navigate = useNavigate();
  const [playlists, setPlaylists] = useState<LibPlaylist[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<LibPlaylist | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    libraryPlaylistsApi
      .list()
      .then(setPlaylists)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleSync = useCallback(async () => {
    setSyncing(true);
    try {
      const result = await libraryPlaylistsApi.sync();
      toast.success(
        `Synced ${result.playlists_synced} playlists, ${result.tracks_synced} tracks`
      );
      load();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Sync failed');
    } finally {
      setSyncing(false);
    }
  }, [load, toast]);

  const handleDeleted = useCallback((id: string) => {
    setPlaylists((prev) => prev.filter((p) => p.id !== id));
    setSelected(null);
  }, []);

  const handleRenamed = useCallback((id: string, name: string) => {
    setPlaylists((prev) =>
      prev.map((p) => (p.id === id ? { ...p, name } : p))
    );
    setSelected((prev) => (prev?.id === id ? { ...prev, name } : prev));
  }, []);

  // Detail view
  if (selected) {
    return (
      <PlaylistDetail
        playlist={selected}
        onBack={() => setSelected(null)}
        onDeleted={handleDeleted}
        onRenamed={handleRenamed}
        toast={toast}
      />
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Library tabs */}
      <div className="px-6 pt-4">
        <div className="flex gap-1 bg-surface p-0.5 rounded-sm w-fit">
          <button
            onClick={() => navigate({ to: '/library' })}
            className="px-4 py-1.5 text-sm font-medium rounded-sm transition-colors capitalize text-text-muted hover:text-text-secondary"
          >
            artists
          </button>
          <button
            onClick={() => navigate({ to: '/library' })}
            className="px-4 py-1.5 text-sm font-medium rounded-sm transition-colors capitalize text-text-muted hover:text-text-secondary"
          >
            albums
          </button>
          <button
            onClick={() => navigate({ to: '/library' })}
            className="px-4 py-1.5 text-sm font-medium rounded-sm transition-colors capitalize text-text-muted hover:text-text-secondary"
          >
            tracks
          </button>
          <button
            className="px-4 py-1.5 text-sm font-medium rounded-sm transition-colors capitalize bg-surface-overlay text-text-primary"
          >
            playlists
          </button>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-border-subtle shrink-0">
        <h1 className="text-lg font-semibold text-text-primary">Playlists</h1>
        <button
          onClick={handleSync}
          disabled={syncing}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-surface-raised hover:bg-border
                     border border-border-input rounded-lg text-xs text-text-muted
                     hover:text-text-primary transition-colors disabled:opacity-50"
        >
          {syncing ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <RefreshCw size={13} />
          )}
          Sync
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {error && <ApiErrorBanner error={error} />}

        {loading && (
          <div className="flex items-center justify-center py-16 text-text-muted">
            <Loader2 size={20} className="animate-spin mr-2" />
            Loading playlists…
          </div>
        )}

        {!loading && !error && playlists.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-text-muted">
            <ListMusic size={40} className="mb-3 opacity-25" />
            <p className="text-sm">No playlists found</p>
            <p className="text-xs mt-1 opacity-60">
              Click Sync to import playlists from your platform
            </p>
          </div>
        )}

        {!loading && playlists.length > 0 && (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
            {playlists.map((pl) => (
              <PlaylistCard
                key={pl.id}
                playlist={pl}
                onClick={() => setSelected(pl)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
