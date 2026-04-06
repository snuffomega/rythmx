import { render, type RenderOptions } from '@testing-library/react'
import { createMemoryHistory, createRouter, RouterProvider } from '@tanstack/react-router'
import { routeTree } from './routeTree.gen'
import type { ReactElement } from 'react'

function createTestRouter() {
  return createRouter({ routeTree, history: createMemoryHistory() })
}

export function renderWithRouter(ui: ReactElement, options?: RenderOptions) {
  const router = createTestRouter()
  return render(
    <RouterProvider router={router} defaultComponent={() => ui} />,
    options,
  )
}

export * from '@testing-library/react'
