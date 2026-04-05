import { useState, useCallback } from 'react';
import { libraryPlaylistsApi } from '../services/api';
import { useToastStore } from '../stores/useToastStore';
import type { LibTrack, LibPlaylist } from '../types';

export interface PlaylistPicker {
  // State
  open: boolean;
  loading: boolean;
  saving: boolean;
  error: string | null;
  track: LibTrack | null;
  selectedPlaylistId: string;
  options: LibPlaylist[];
  // Actions
  openPicker: (track: LibTrack) => void;
  closePicker: () => void;
  confirmAdd: () => void;
  setSelectedPlaylistId: (id: string) => void;
}

export function usePlaylistPicker(): PlaylistPicker {
  const toastSuccess = useToastStore(s => s.success);
  const toastError = useToastStore(s => s.error);

  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [track, setTrack] = useState<LibTrack | null>(null);
  const [selectedPlaylistId, setSelectedPlaylistId] = useState('');
  const [options, setOptions] = useState<LibPlaylist[]>([]);

  const openPicker = useCallback(async (t: LibTrack) => {
    setTrack(t);
    setOpen(true);
    setLoading(true);
    setSaving(false);
    setError(null);
    try {
      const list = await libraryPlaylistsApi.list();
      setOptions(list);
      setSelectedPlaylistId(list[0]?.id ?? '');
      if (list.length === 0) {
        setError('No playlists found. Sync or create one in Library > Playlists first.');
      }
    } catch (err) {
      setOptions([]);
      setSelectedPlaylistId('');
      setError(err instanceof Error ? err.message : 'Failed to load playlists');
    } finally {
      setLoading(false);
    }
  }, []);

  const closePicker = useCallback(() => {
    setOpen(false);
    setLoading(false);
    setSaving(false);
    setError(null);
    setTrack(null);
    setSelectedPlaylistId('');
  }, []);

  const confirmAdd = useCallback(async () => {
    if (!track || !selectedPlaylistId) return;
    setSaving(true);
    setError(null);
    try {
      const result = await libraryPlaylistsApi.addTracks(selectedPlaylistId, [track.id]);
      const selected = options.find(p => p.id === selectedPlaylistId);
      toastSuccess(
        `Added "${track.title}" to "${selected?.name ?? selectedPlaylistId}" (${result.track_count} tracks)`
      );
      closePicker();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to add track to playlist';
      setError(msg);
      toastError(msg);
    } finally {
      setSaving(false);
    }
  }, [track, selectedPlaylistId, options, toastSuccess, toastError, closePicker]);

  return {
    open,
    loading,
    saving,
    error,
    track,
    selectedPlaylistId,
    options,
    openPicker,
    closePicker,
    confirmAdd,
    setSelectedPlaylistId,
  };
}
