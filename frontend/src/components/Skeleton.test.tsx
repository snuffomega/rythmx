import { render } from '@testing-library/react'
import { RowSkeleton, CardSkeleton } from './Skeleton'

describe('RowSkeleton', () => {
  it('renders without crashing', () => {
    const { container } = render(<RowSkeleton />)
    expect(container.firstChild).toBeTruthy()
  })
})

describe('CardSkeleton', () => {
  it('renders without crashing', () => {
    const { container } = render(<CardSkeleton />)
    expect(container.firstChild).toBeTruthy()
  })
})
