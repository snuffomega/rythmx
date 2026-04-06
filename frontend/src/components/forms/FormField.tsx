import type { ReactNode } from 'react'

interface FormFieldProps {
  label?: string
  helperText?: string
  htmlFor?: string
  children: ReactNode
}

export function FormField({ label, helperText, htmlFor, children }: FormFieldProps) {
  return (
    <div>
      {label && <label className="label" htmlFor={htmlFor}>{label}</label>}
      {children}
      {helperText && <p className="text-text-muted text-xs mt-1">{helperText}</p>}
    </div>
  )
}
