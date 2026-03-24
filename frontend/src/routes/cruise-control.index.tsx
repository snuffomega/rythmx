import { createFileRoute, redirect } from '@tanstack/react-router';

export const Route = createFileRoute('/cruise-control/')({
  beforeLoad: () => {
    throw redirect({ to: '/forge/new-music' });
  },
});
