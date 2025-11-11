import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeHighlight from 'rehype-highlight'
import 'katex/dist/katex.min.css'

type Props = {
  className?: string
  children: string
}

export function Markdown({ className, children }: Props) {
  return (
    <div className={['prose prose-sm max-w-none', 'prose-p:whitespace-pre-wrap prose-p:break-words', 'prose-code:font-mono prose-pre:bg-bgColumn prose-pre:border prose-pre:border-bgCardAlt', 'text-gray-100', className].filter(Boolean).join(' ')}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[[rehypeKatex, { strict: false }], rehypeHighlight]}>
        {children}
      </ReactMarkdown>
    </div>
  )
}

export default Markdown

