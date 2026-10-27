import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeHighlight from 'rehype-highlight'
import { cn } from '../../lib/utils'
import 'katex/dist/katex.min.css'

type MarkdownMessageVariant = 'assistant' | 'user' | 'system'

interface MarkdownMessageProps {
  content: string
  variant?: MarkdownMessageVariant
  className?: string
}

export function MarkdownMessage({ content, variant = 'assistant', className }: MarkdownMessageProps) {
  const normalizedContent = useMemo(() =>
    content
      .replace(/\\\(/g, '$')
      .replace(/\\\)/g, '$')
      .replace(/\\\[/g, '$$')
      .replace(/\\\]/g, '$$'),
  [content])

  const variantClass = variant === 'user'
    ? 'prose-invert prose-p:text-white prose-headings:text-white prose-strong:text-white prose-em:text-white prose-a:text-blue-200 prose-a:hover:text-blue-100 prose-li:text-white prose-blockquote:text-white/90 prose-code:text-white prose-code:bg-blue-500/30 prose-pre:bg-blue-600/20 prose-pre:border-blue-200/40 [&_.katex]:text-white'
    : variant === 'system'
      ? 'dark:prose-invert prose-strong:text-muted-foreground prose-headings:text-muted-foreground prose-em:text-muted-foreground prose-a:text-blue-500 prose-a:hover:underline'
      : 'dark:prose-invert prose-strong:text-foreground prose-headings:text-foreground prose-em:text-foreground prose-a:text-blue-500 prose-a:hover:underline'

  return (
    <div
      className={cn(
        'prose prose-sm max-w-none',
        'prose-p:whitespace-pre-wrap prose-p:wrap-break-word prose-p:my-1 first:prose-p:mt-0 last:prose-p:mb-0 prose-p:leading-relaxed',
        'prose-pre:my-3 prose-pre:rounded-lg prose-pre:border prose-pre:border-border prose-pre:bg-background prose-pre:px-3 prose-pre:py-2 prose-pre:overflow-x-auto dark:prose-pre:bg-muted/30 dark:prose-pre:border-muted/60',
        'prose-code:text-sm prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded-md prose-code:bg-background prose-code:text-foreground dark:prose-code:bg-muted/30 dark:prose-code:text-foreground',
        'prose-code:before:content-none prose-code:after:content-none',
        '[&_pre_code]:bg-transparent [&_pre_code]:px-0 [&_pre_code]:py-0',
        'prose-ul:my-2 prose-ol:my-2 prose-li:my-0.5',
        'prose-blockquote:border-l-4 prose-blockquote:border-muted prose-blockquote:pl-4 prose-blockquote:italic',
        'prose-headings:font-semibold prose-headings:mt-3 prose-headings:mb-2',
        variantClass,
        variant === 'assistant' ? '[&_.katex]:text-foreground dark:[&_.katex]:text-foreground' : undefined,
        className
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { strict: false }], rehypeHighlight]}
      >
        {normalizedContent}
      </ReactMarkdown>
    </div>
  )
}
