import { createFileRoute } from '@tanstack/react-router';
import { ArtistDetail } from '../components/library/ArtistDetail';

export const Route = createFileRoute('/library/artist/$id')({
  component: ArtistDetailRoute,
});

function ArtistDetailRoute() {
  const { id } = Route.useParams();
  return (
    <div className="flex flex-col h-full overflow-hidden">
      <ArtistDetail artistId={id} />
    </div>
  );
}
