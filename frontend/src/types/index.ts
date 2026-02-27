export type Period = '7day' | '1month' | '3month' | '6month' | '12month' | 'overall';
export type AcquisitionStatus = 'pending' | 'submitted' | 'found' | 'failed' | 'skipped';
export type ReleaseKind = 'album' | 'ep' | 'single' | 'compilation';
export type LibraryBackend = 'soulsync' | 'plex' | 'jellyfin' | 'navidrome';

export interface Artist {
  name: string;
  image?: string;
  playcount?: number;
  url?: string;
}

export interface Album {
  artist: string;
  title: string;
  image?: string;
  release_date?: string;
  mbid?: string;
}

export interface TopAlbum {
  artist: string;
  title: string;
  image?: string;
  playcount?: number;
}

export interface Track {
  name: string;
  artist: string;
  album?: string;
  image?: string;
  playcount?: number;
  duration?: number;
  is_owned?: boolean;
  score?: number;
  rank?: number;
}

export interface QueueItem {
  id: string | number;
  artist: string;
  album: string;
  kind: ReleaseKind;
  status: AcquisitionStatus;
  requested_by?: string;
  requested_at?: string;
  release_date?: string;
}

export interface AcquisitionStats {
  pending: number;
  submitted: number;
  found: number;
  failed: number;
  skipped: number;
  total: number;
}

export type PlaylistSource = 'taste' | 'lastfm' | 'spotify' | 'deezer' | 'empty';

export interface PlaylistItem {
  name: string;
  source?: PlaylistSource;
  last_synced?: string;
  created_at?: string;
  mode?: string;
  auto_sync?: boolean;
  track_count?: number;
  owned_count?: number;
  max_tracks?: number;
  source_url?: string;
}

export interface PlaylistTrack {
  name: string;
  artist: string;
  album?: string;
  image?: string;
  is_owned: boolean;
  score?: number;
}

export interface CruiseControlStatus {
  state: 'idle' | 'running' | 'error' | 'completed';
  stage?: number;
  stage_label?: string;
  total_stages?: number;
  last_run?: string;
  summary?: {
    artists_checked: number;
    new_releases: number;
    owned: number;
    queued: number;
  };
  error?: string;
}

export interface CruiseControlConfig {
  cc_run_mode: 'playlist' | 'cruise';
  cc_playlist_prefix: string;
  cc_min_listens: number;
  cc_period: Period;
  cc_lookback_days: number;
  cc_max_per_cycle: number;
  cc_auto_push_playlist: boolean;
  cc_schedule_weekday: number;
  cc_schedule_hour: number;
  cc_enabled: boolean;
  cc_dry_run: boolean;
  release_cache_refresh_weekday: number;
  release_cache_refresh_hour: number;
  nr_ignore_keywords: string;
  nr_ignore_artists: string;
}

export interface HistoryItem {
  artist: string;
  album: string;
  status: 'owned' | 'queued' | 'skipped';
  date: string;
}

export interface PersonalDiscoveryConfig {
  closeness: number;
  seed_period: '7day' | '1month' | '3month' | '6month' | '12month' | 'overall';
  min_scrobbles: number;
  max_tracks: number;
}

export interface PersonalDiscoveryResult {
  artist: string;
  image?: string;
  reason?: string;
  similarity?: number;
  tags?: string[];
}

export interface StatsSummary {
  top_artist: string;
  total_artists: number;
  total_plays?: number;
}

export interface LibraryStatus {
  backend: LibraryBackend;
  track_count: number;
  last_synced?: string;
}

export interface ConnectionStatus {
  connected: boolean;
  message?: string;
}

export interface Settings {
  lastfm_username?: string;
  lastfm_api_key?: string;
  plex_url?: string;
  plex_token?: string;
  soulsync_url?: string;
  spotify_client_id?: string;
  spotify_client_secret?: string;
  library_backend: LibraryBackend;
}

export interface Toast {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
}
