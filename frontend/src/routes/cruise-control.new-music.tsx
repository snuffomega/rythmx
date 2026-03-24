import { createFileRoute, redirect } from '@tanstack/react-router';

export const Route = createFileRoute('/cruise-control/new-music')({
  beforeLoad: () => {
    throw redirect({ to: '/forge/new-music' });
  },
  component: () => null,
});
