import { createFileRoute } from '@tanstack/react-router';
import { ForgeSync } from '../pages/ForgeSync';

export const Route = createFileRoute('/forge/sync')({
  component: ForgeSync,
});
