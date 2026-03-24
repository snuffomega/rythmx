import { useState } from 'react';
import { Plus, Loader2, ListMusic } from 'lucide-react';
import type { PlaylistItem, PlaylistSource } from '../../types';

type NewPlaylistSource = Exclude<PlaylistSource, 'taste'>;

const SOURCE_OPTIONS: Array<{ value: NewPlaylistSource; label: string; description: string }> = [
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

interface InlineNewPlaylistFormProps {
  onCreate: (data: Partial<PlaylistItem>) => Promise<void>;
}

export function InlineNewPlaylistForm({ onCreate }: InlineNewPlaylistFormProps) {
  const [name, setName] = useState('My Playlist');
  const [source, setSource] = useState<NewPlaylistSource>('spotify');
  const [url, setUrl] = useState('');
  const [maxTracks, setMaxTracks] = useState(50);
  const [creating, setCreating] = useState(false);

  const handleSubmit = async () => {
    if (!name.trim()) return;
    setCreating(true);
    await onCreate({
      name: name.trim(),
      source,
      source_url: url.trim() || undefined,
      max_tracks: maxTracks,
    }).finally(() => setCreating(false));
  };

  return (
    <div className="bg-[#0e0e0e] border border-[#1a1a1a]">
      <div className="flex items-center gap-2 px-5 py-4 border-b border-[#1a1a1a]">
        <ListMusic size={14} className="text-accent" />
        <span className="text-[#444] text-xs font-semibold uppercase tracking-widest">New Playlist</span>
      </div>

      <div className="p-5 space-y-5">
        <div>
          <label className="label">Playlist Name</label>
          <input
            className="input"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="My Playlist"
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
          className="btn-primary flex items-center justify-center gap-2"
        >
          {creating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
          Create Playlist
        </button>
      </div>
    </div>
  );
}
