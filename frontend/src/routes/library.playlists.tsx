import { createFileRoute } from '@tanstack/react-router';
import { LibraryPlaylists } from '../pages/LibraryPlaylists';
import { useToastStore } from '../stores/useToastStore';

export const Route = createFileRoute('/library/playlists')({
  component: LibraryPlaylistsRoute,
});

function LibraryPlaylistsRoute() {
  const success = useToastStore((s) => s.success);
  const error = useToastStore((s) => s.error);
  return <LibraryPlaylists toast={{ success, error }} />;
}
