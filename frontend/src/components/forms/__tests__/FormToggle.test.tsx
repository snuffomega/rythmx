import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { FormToggle } from '../FormToggle'

describe('FormToggle', () => {
  it('renders label', () => {
    render(<FormToggle label="Auto Publish" on={false} onChange={() => {}} />)
    expect(screen.getByText('Auto Publish')).toBeTruthy()
  })

  it('renders description when provided', () => {
    render(<FormToggle label="Auto Publish" description="Publish on approval" on={false} onChange={() => {}} />)
    expect(screen.getByText('Publish on approval')).toBeTruthy()
  })

  it('omits description when not provided', () => {
    render(<FormToggle label="Auto Publish" on={false} onChange={() => {}} />)
    expect(screen.queryByText('Publish on approval')).toBeNull()
  })

  it('calls onChange with true when toggled off', () => {
    const onChange = vi.fn()
    render(<FormToggle label="Toggle me" on={false} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button'))
    expect(onChange).toHaveBeenCalledWith(true)
  })

  it('calls onChange with false when toggled on', () => {
    const onChange = vi.fn()
    render(<FormToggle label="Toggle me" on={true} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button'))
    expect(onChange).toHaveBeenCalledWith(false)
  })

  it('does not fire onChange when disabled', () => {
    const onChange = vi.fn()
    render(<FormToggle label="Toggle me" on={false} onChange={onChange} disabled />)
    fireEvent.click(screen.getByRole('button'))
    expect(onChange).not.toHaveBeenCalled()
  })
})
