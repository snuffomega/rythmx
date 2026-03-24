import { useApi } from '../hooks/useApi';
import { playlistsApi } from '../services/api';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ApiErrorBanner } from '../components/common';
import { PlaylistCard, InlineNewPlaylistForm } from '../components/forge';
import { useState } from 'react';
import { ListMusic } from 'lucide-react';
import type { PlaylistItem } from '../types';

interface ForgeSyncProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function ForgeSync({ toast }: ForgeSyncProps) {
  const { data: playlists, loading, error: playlistsError, refetch } = useApi(() => playlistsApi.getAll());
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const handleCreate = async (data: Partial<PlaylistItem>) => {
    try {
      await playlistsApi.create(data);
      toast.success(`Playlist "${data.name}" created`);
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
    <div className="space-y-8">
      {/* Workspace: new playlist form */}
      <InlineNewPlaylistForm onCreate={handleCreate} />

      {/* History ledger: existing playlists */}
      <section>
        <div className="pb-3 border-b border-[#1a1a1a] mb-4">
          <span className="text-[#444] text-xs font-semibold uppercase tracking-widest">Playlists</span>
        </div>

        {playlistsError ? (
          <ApiErrorBanner error={playlistsError} onRetry={refetch} />
        ) : loading ? (
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
          <div className="text-center py-16">
            <ListMusic size={32} className="text-[#1e1e1e] mx-auto mb-3" />
            <p className="text-[#444] text-sm">No playlists yet</p>
            <p className="text-[#333] text-xs mt-1">Create one above to get started</p>
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
      </section>

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
