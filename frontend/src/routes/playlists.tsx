import { createFileRoute } from '@tanstack/react-router';
import { Playlists } from '../pages/Playlists';
import { useToastStore } from '../stores/useToastStore';

export const Route = createFileRoute('/playlists')({
  component: PlaylistsRoute,
});

function PlaylistsRoute() {
  const toast = {
    success: useToastStore(s => s.success),
    error: useToastStore(s => s.error),
  };
  return <Playlists toast={toast} />;
}
