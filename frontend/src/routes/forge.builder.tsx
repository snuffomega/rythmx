import { createFileRoute } from '@tanstack/react-router';
import { ForgeBuilder } from '../pages/ForgeBuilder';

export const Route = createFileRoute('/forge/builder')({
  component: ForgeBuilder,
});
