import { X } from 'lucide-react'
import { useToastStore } from '../../stores/toastStore'

const toneStyles: Record<'success' | 'error' | 'info', { container: string; text: string; indicator: string }> = {
  success: {
    container: 'border-emerald-500/40 bg-emerald-500/10',
    text: 'text-emerald-700 dark:text-emerald-300',
    indicator: 'bg-emerald-500',
  },
  error: {
    container: 'border-destructive/40 bg-destructive/10',
    text: 'text-destructive',
    indicator: 'bg-destructive',
  },
  info: {
    container: 'border-primary/30 bg-primary/10',
    text: 'text-primary-700 dark:text-primary-200',
    indicator: 'bg-primary-500',
  },
}

export function ToastContainer() {
  const toasts = useToastStore((state) => state.toasts)
  const dismissToast = useToastStore((state) => state.dismissToast)

  if (toasts.length === 0) {
    return null
  }

  return (
    <div className="pointer-events-none fixed top-4 right-4 z-50 flex w-80 flex-col gap-2">
      {toasts.map((toast) => {
        const { container, text, indicator } = toneStyles[toast.tone] ?? toneStyles.info
        return (
          <div
            key={toast.id}
            className={`pointer-events-auto rounded-lg border px-4 py-3 shadow-lg backdrop-blur-sm ${container}`}
          >
            <div className="flex items-start gap-3">
              <div className={`mt-1 h-2 w-2 rounded-full ${indicator}`} />
              <div className="flex-1">
                <div className={`text-sm font-medium leading-snug ${text}`}>{toast.message}</div>
                {toast.action && (
                  <button
                    type="button"
                    onClick={() => {
                      toast.action?.onClick()
                      dismissToast(toast.id)
                    }}
                    className="mt-1 text-xs font-medium text-primary hover:underline"
                  >
                    {toast.action.label}
                  </button>
                )}
              </div>
              <button
                type="button"
                onClick={() => dismissToast(toast.id)}
                className="ml-2 inline-flex h-6 w-6 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
