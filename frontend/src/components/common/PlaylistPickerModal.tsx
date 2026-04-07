import { Loader2 } from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import type { PlaylistPicker } from '../../hooks/usePlaylistPicker';

interface PlaylistPickerModalProps extends PlaylistPicker {}

export function PlaylistPickerModal(picker: PlaylistPickerModalProps) {
  const navigate = useNavigate();

  if (!picker.open) return null;

  return (
    <div className="fixed inset-0 z-40 bg-black/60 flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-base border border-border-input p-4">
        <h3 className="text-sm font-semibold text-text-primary">Add To Playlist</h3>
        <p className="text-xs text-text-muted mt-1 truncate">
          {picker.track ? `Track: ${picker.track.title}` : 'Select a playlist'}
        </p>

        {picker.loading ? (
          <div className="flex items-center gap-2 text-xs text-text-muted mt-4">
            <Loader2 size={12} className="animate-spin" />
            Loading playlists...
          </div>
        ) : (
          <>
            <div className="mt-4">
              <label className="block text-[10px] font-mono text-text-muted uppercase tracking-wider mb-1.5">
                Playlist
              </label>
              <select
                value={picker.selectedPlaylistId}
                onChange={e => picker.setSelectedPlaylistId(e.target.value)}
                className="w-full bg-surface border border-border-input text-text-primary text-sm px-3 py-2 focus:outline-none focus:border-accent"
                disabled={picker.options.length === 0 || picker.saving}
              >
                {picker.options.length === 0 ? (
                  <option value="">No playlists available</option>
                ) : (
                  picker.options.map(pl => (
                    <option key={pl.id} value={pl.id}>
                      {pl.name} ({pl.track_count} tracks)
                    </option>
                  ))
                )}
              </select>
            </div>

            {picker.error && (
              <p className="text-xs text-danger mt-3">{picker.error}</p>
            )}
          </>
        )}

        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            onClick={picker.closePicker}
            disabled={picker.saving}
            className="px-3 py-1.5 text-xs border border-border-strong text-text-muted hover:text-text-primary transition-colors disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            onClick={() => navigate({ to: '/library/playlists' })}
            disabled={picker.saving}
            className="px-3 py-1.5 text-xs border border-border-strong text-text-muted hover:text-text-primary transition-colors disabled:opacity-40"
          >
            Open Playlists
          </button>
          <button
            onClick={picker.confirmAdd}
            disabled={
              picker.loading ||
              picker.saving ||
              !picker.track ||
              !picker.selectedPlaylistId
            }
            className="px-3 py-1.5 text-xs bg-accent text-black hover:bg-accent/80 transition-colors disabled:opacity-40"
          >
            {picker.saving ? 'Adding...' : 'Add'}
          </button>
        </div>
      </div>
    </div>
  );
}
