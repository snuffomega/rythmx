import { createFileRoute } from '@tanstack/react-router';
import { LibraryRoot } from '../pages/Library';

export const Route = createFileRoute('/library/')({
  component: LibraryRoot,
});
