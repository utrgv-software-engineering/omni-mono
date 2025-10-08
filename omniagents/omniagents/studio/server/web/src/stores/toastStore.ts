import { create } from 'zustand'

type ToastTone = 'info' | 'success' | 'error'

type ToastRecord = {
  id: string
  message: string
  tone: ToastTone
  duration: number
}

interface ToastState {
  toasts: ToastRecord[]
  pushToast: (options: { message: string; tone?: ToastTone; duration?: number }) => string
  dismissToast: (id: string) => void
}

const generateId = () => {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  return Math.random().toString(36).slice(2)
}

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  pushToast: ({ message, tone = 'info', duration = 4000 }) => {
    const id = generateId()
    set((state) => ({
      toasts: [...state.toasts, { id, message, tone, duration }],
    }))
    if (duration > 0 && typeof window !== 'undefined') {
      window.setTimeout(() => {
        const dismissToast = get().dismissToast
        dismissToast(id)
      }, duration)
    }
    return id
  },
  dismissToast: (id) => {
    set((state) => ({
      toasts: state.toasts.filter((toast) => toast.id !== id),
    }))
  },
}))
