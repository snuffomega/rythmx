import { createFileRoute } from '@tanstack/react-router';
import { ForgeNewMusic } from '../pages/ForgeNewMusic';

export const Route = createFileRoute('/forge/new-music')({
  component: ForgeNewMusic,
});
