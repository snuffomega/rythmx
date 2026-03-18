// All backend responses follow { status: 'ok', ...payload } or { status: 'error', error: string }.
// The request() function in api.ts throws on status='error', so callers always receive the ok shape.
export type ApiOkResponse = { status: 'ok' };
export type ApiErrorResponse = { status: 'error'; error?: string; message?: string };

export type Period = '7day' | '1month' | '3month' | '6month' | '12month' | 'overall';
export type AcquisitionStatus = 'pending' | 'submitted' | 'found' | 'failed' | 'skipped';
export type ReleaseKind = 'album' | 'ep' | 'single' | 'compilation';
export type LibraryPlatform = 'plex' | 'jellyfin' | 'navidrome';

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

export type ImageType = 'artist' | 'album' | 'track';

export interface ImageResolveResponse {
  image_url?: string;
  pending?: boolean;
}

export interface CollageItem {
  name: string;
  artist?: string;
  image?: string;
  playcount?: number;
}

export type PlaylistSource = 'taste' | 'lastfm' | 'spotify' | 'deezer' | 'empty' | 'new_music';

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
  row_id?: number;
  name: string;
  artist: string;
  album?: string;
  image?: string;
  is_owned: boolean;
  score?: number;
  acquisition_status?: AcquisitionStatus | null;
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
  run_mode: 'build' | 'fetch';
  playlist_prefix: string;
  min_listens: number;
  period: Period;
  lookback_days: number;
  max_per_cycle: number;
  max_playlist_tracks: number;
  auto_push_playlist: boolean;
  schedule_weekday: number;
  schedule_hour: number;
  enabled: boolean;
  dry_run: boolean;
  release_cache_refresh_weekday: number;
  release_cache_refresh_hour: number;
  nr_ignore_keywords: string;
  nr_ignore_artists: string;
  include_features: boolean;
  release_kinds: string;
}

export interface HistoryItem {
  artist: string;
  album: string;
  status: 'owned' | 'queued' | 'skipped';
  reason?: string;
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
  platform: LibraryPlatform;
  track_count: number;
  last_synced?: string;
  synced?: boolean;
  total_albums?: number;
  enriched_albums?: number;
  enrich_pct?: number;
}

export interface LibraryEnrichStatus extends LibraryStatus {
  enrich_running: boolean;
}

export interface SpotifyEnrichStatus {
  enrich_running: boolean;
  enriched_artists: number;
  total_artists: number;
  last_run?: string | null;
  spotify_available: boolean;
}

export interface LastfmTagsStatus {
  enrich_running: boolean;
  enriched_artists: number;
  total_artists: number;
  enriched_albums: number;
  total_albums: number;
  last_run?: string | null;
  lastfm_available: boolean;
}

export interface DeezerBpmStatus {
  enrich_running: boolean;
  enriched_albums: number;
  total_albums: number;
  enriched_tracks: number;
  total_tracks: number;
  last_run?: string | null;
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
  library_platform: LibraryPlatform;
}

export interface Toast {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
}

// ---------------------------------------------------------------------------
// Library browse — lib_* table shapes
// ---------------------------------------------------------------------------

export interface LibArtist {
  id: string;
  name: string;
  album_count: number;
  match_confidence: number;
  source_backend: string | null;
  lastfm_tags_json: string | null;
}

export interface LibAlbum {
  id: string;
  artist_id: string;
  artist_name: string;
  title: string;
  year: number | null;
  record_type: string | null;
  match_confidence: number;
  needs_verification: number;
  source_backend: string | null;
}

export interface LibTrack {
  id: string;
  album_id: string;
  artist_id: string;
  title: string;
  track_number: number | null;
  disc_number: number | null;
  duration: number | null;
  rating: number;
  play_count: number;
  album_title?: string;
  artist_name?: string;
}

export interface LibArtistDetail {
  artist: LibArtist;
  albums: LibAlbum[];
  top_tracks: LibTrack[];
}

export interface LibAlbumDetail {
  album: LibAlbum;
  tracks: LibTrack[];
}

// ---------------------------------------------------------------------------
// Library Audit
// ---------------------------------------------------------------------------

export interface AuditEnrichmentMeta {
  status: 'found' | 'not_found' | 'error' | 'fallback' | 'pending';
  confidence: number | null;
}

export interface AuditItem {
  artist_id: string;
  artist_name: string;
  album_id: string;
  album_title: string;
  match_confidence: number | null;
  needs_verification: boolean;
  itunes_album_id: string | null;
  deezer_id: string | null;
  enrichment: Record<string, AuditEnrichmentMeta>;
}

export interface AuditResponse {
  status: 'ok';
  items: AuditItem[];
  total: number;
  page: number;
}
