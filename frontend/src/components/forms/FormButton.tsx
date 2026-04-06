import { Loader2 } from 'lucide-react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'

const VARIANT_CLASS = {
  primary: 'btn-primary',
  secondary: 'btn-secondary',
  danger: 'btn-danger',
} as const

interface FormButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  loading?: boolean
  variant?: keyof typeof VARIANT_CLASS
  icon?: ReactNode
}

export function FormButton({ children, loading, variant = 'secondary', icon, className, disabled, ...props }: FormButtonProps) {
  return (
    <button
      type="button"
      className={`${VARIANT_CLASS[variant]} inline-flex items-center gap-2 disabled:opacity-40${className ? ` ${className}` : ''}`}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? <Loader2 size={12} className="animate-spin" /> : (icon ?? null)}
      {children}
    </button>
  )
}
