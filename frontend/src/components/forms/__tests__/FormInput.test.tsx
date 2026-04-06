import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { FormInput } from '../FormInput'

describe('FormInput', () => {
  it('renders input without label', () => {
    render(<FormInput placeholder="Enter value" />)
    expect(screen.getByPlaceholderText('Enter value')).toBeTruthy()
  })

  it('renders label when provided', () => {
    render(<FormInput label="Build Name" />)
    expect(screen.getByText('Build Name')).toBeTruthy()
  })

  it('associates label with input via htmlFor', () => {
    render(<FormInput label="Build Name" />)
    const label = screen.getByText('Build Name') as HTMLLabelElement
    const input = screen.getByRole('textbox')
    expect(label.htmlFor).toBe(input.id)
  })

  it('renders helper text', () => {
    render(<FormInput label="Field" helperText="Some hint" />)
    expect(screen.getByText('Some hint')).toBeTruthy()
  })

  it('calls onChange when typing', async () => {
    const onChange = vi.fn()
    render(<FormInput label="Field" onChange={onChange} />)
    await userEvent.type(screen.getByRole('textbox'), 'hello')
    expect(onChange).toHaveBeenCalled()
  })

  it('applies input class', () => {
    render(<FormInput data-testid="inp" />)
    expect(screen.getByTestId('inp').className).toContain('input')
  })

  it('merges extra className', () => {
    render(<FormInput data-testid="inp" className="max-w-[220px]" />)
    const el = screen.getByTestId('inp')
    expect(el.className).toContain('input')
    expect(el.className).toContain('max-w-[220px]')
  })
})
