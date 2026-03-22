import { createFileRoute } from '@tanstack/react-router';
import { AlbumDetail } from '../pages/Library';

export const Route = createFileRoute('/library/album/$id')({
  component: AlbumDetailRoute,
});

function AlbumDetailRoute() {
  const { id } = Route.useParams();
  return (
    <div className="flex flex-col h-full overflow-hidden">
      <AlbumDetail albumId={id} />
    </div>
  );
}
