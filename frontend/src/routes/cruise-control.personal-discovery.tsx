import { createFileRoute, redirect } from '@tanstack/react-router';

export const Route = createFileRoute('/cruise-control/personal-discovery')({
  beforeLoad: () => {
    throw redirect({ to: '/forge/custom-discovery' });
  },
  component: () => null,
});
