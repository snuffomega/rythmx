import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { FormSelect } from '../FormSelect'

const OPTIONS = [
  { value: '1w', label: 'Last week' },
  { value: '1m', label: 'Last month' },
  { value: '3m', label: 'Last 3 months' },
]

describe('FormSelect', () => {
  it('renders all options', () => {
    render(<FormSelect options={OPTIONS} />)
    expect(screen.getByRole('option', { name: 'Last week' })).toBeTruthy()
    expect(screen.getByRole('option', { name: 'Last month' })).toBeTruthy()
    expect(screen.getByRole('option', { name: 'Last 3 months' })).toBeTruthy()
  })

  it('renders label', () => {
    render(<FormSelect label="Scrobbling Period" options={OPTIONS} />)
    expect(screen.getByText('Scrobbling Period')).toBeTruthy()
  })

  it('associates label with select via htmlFor', () => {
    render(<FormSelect label="Period" options={OPTIONS} />)
    const label = screen.getByText('Period') as HTMLLabelElement
    const select = screen.getByRole('combobox')
    expect(label.htmlFor).toBe(select.id)
  })

  it('renders helper text', () => {
    render(<FormSelect options={OPTIONS} helperText="Choose a range" />)
    expect(screen.getByText('Choose a range')).toBeTruthy()
  })

  it('calls onChange on selection', async () => {
    const onChange = vi.fn()
    render(<FormSelect options={OPTIONS} onChange={onChange} />)
    await userEvent.selectOptions(screen.getByRole('combobox'), '1m')
    expect(onChange).toHaveBeenCalled()
  })

  it('applies select class', () => {
    render(<FormSelect options={OPTIONS} data-testid="sel" />)
    expect(screen.getByTestId('sel').className).toContain('select')
  })
})
