import { createFileRoute, redirect } from '@tanstack/react-router';

export const Route = createFileRoute('/forge/')({
  beforeLoad: () => {
    throw redirect({ to: '/forge/builder' });
  },
});
