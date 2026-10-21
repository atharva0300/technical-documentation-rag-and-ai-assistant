import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface MarkdownMessageProps {
  content: string
  // Called with the citation number when an inline [n] citation is clicked
  onCitationClick?: (citationId: number) => void
}

const CITATION_HREF_PREFIX = '#cite-'

// Turns inline citations like [2] into markdown links (#cite-2) so they render as
// clickable chips, leaving fenced and inline code alone so things like `args[1]` keep
// their brackets.
function linkCitations(markdown: string): string {
  return markdown
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((part, i) =>
      i % 2 === 1 ? part : part.replace(/\[(\d+)\](?!\()/g, `[$1](${CITATION_HREF_PREFIX}$1)`)
    )
    .join('')
}

export default function MarkdownMessage({ content, onCitationClick }: MarkdownMessageProps) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith(CITATION_HREF_PREFIX)) {
              const citationId = Number(href.slice(CITATION_HREF_PREFIX.length))
              return (
                <button
                  type="button"
                  className="citation-ref"
                  onClick={() => onCitationClick?.(citationId)}
                  title={`Show source ${citationId}`}
                >
                  {citationId}
                </button>
              )
            }
            return (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            )
          }
        }}
      >
        {linkCitations(content)}
      </ReactMarkdown>
    </div>
  )
}
