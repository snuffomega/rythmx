import { createFileRoute } from '@tanstack/react-router';
import { ReleaseDetailView } from '../pages/Library';

export const Route = createFileRoute('/library/release/$id')({
  component: ReleaseDetailRoute,
});

function ReleaseDetailRoute() {
  const { id } = Route.useParams();
  return (
    <div className="flex flex-col h-full overflow-hidden">
      <ReleaseDetailView releaseId={id} />
    </div>
  );
}
