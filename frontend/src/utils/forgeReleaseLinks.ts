import type { DiscoveredRelease } from '../types';

type ForgeReleaseTarget =
  | { kind: 'library-artist'; artistId: string }
  | { kind: 'external'; url: string };

export function getForgeReleaseTarget(release: DiscoveredRelease): ForgeReleaseTarget | null {
  const libraryArtistId = String(release.library_artist_id ?? '').trim();
  if (libraryArtistId) {
    return { kind: 'library-artist', artistId: libraryArtistId };
  }

  const releaseId = String(release.id ?? '').trim();
  if (releaseId) {
    return {
      kind: 'external',
      url: `https://www.deezer.com/album/${encodeURIComponent(releaseId)}`,
    };
  }

  const artistId = String(release.artist_deezer_id ?? '').trim();
  if (artistId) {
    return {
      kind: 'external',
      url: `https://www.deezer.com/artist/${encodeURIComponent(artistId)}`,
    };
  }

  return null;
}

let lastExternalUrl = '';
let lastExternalOpenAt = 0;

export function openExternalReleaseUrl(url: string): void {
  const now = Date.now();
  if (lastExternalUrl === url && now - lastExternalOpenAt < 800) {
    return;
  }

  lastExternalUrl = url;
  lastExternalOpenAt = now;
  window.open(url, '_blank', 'noopener,noreferrer');
}
