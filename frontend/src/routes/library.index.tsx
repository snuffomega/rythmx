import { createFileRoute } from '@tanstack/react-router';
import { LibraryRoot } from '../components/library/LibraryRoot';

export const Route = createFileRoute('/library/')({
  component: LibraryRoot,
});
