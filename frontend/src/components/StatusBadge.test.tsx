import { render, screen } from '@testing-library/react'
import { StatusBadge } from './StatusBadge'

describe('StatusBadge', () => {
  it('renders mapped label for known status', () => {
    render(<StatusBadge status="owned" />)
    expect(screen.getByText('Owned')).toBeInTheDocument()
  })

  it('renders raw status string for unknown status', () => {
    render(<StatusBadge status="custom-status" />)
    expect(screen.getByText('custom-status')).toBeInTheDocument()
  })

  it('applies danger class for failed status', () => {
    render(<StatusBadge status="failed" />)
    expect(screen.getByText('Failed')).toHaveClass('badge-danger')
  })
})
