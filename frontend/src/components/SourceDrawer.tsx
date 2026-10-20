import React from 'react'

export interface SourceItem {
  source_id?: number
  document_id?: string
  title?: string
  section?: string
  source?: string
  similarity?: number
  content?: string
}

interface SourceDrawerProps {
  sources: SourceItem[]
  activeSourceId: number | null
}

export default function SourceDrawer({ sources, activeSourceId }: SourceDrawerProps) {
  if (!sources || sources.length === 0) {
    return (
      <aside className="source-drawer">
        <h3 className="label-title">Retrieved Sources</h3>
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
          No sources cited for this response yet.
        </p>
      </aside>
    )
  }

  return (
    <aside className="source-drawer">
      <h3 className="label-title">Retrieved Grounded Sources ({sources.length})</h3>
      {sources.map((src, idx) => {
        const id = src.source_id || idx + 1
        const isHighlight = activeSourceId === id
        return (
          <div
            key={idx}
            className="source-card"
            style={{
              borderColor: isHighlight ? 'var(--accent-cyan)' : 'var(--border-color)',
              boxShadow: isHighlight ? '0 0 10px rgba(6, 182, 212, 0.3)' : 'none'
            }}
          >
            <div className="source-card-header">
              [{id}] {src.title || 'Document'}
            </div>
            {src.section && (
              <div className="source-card-section">
                Section: {src.section}
              </div>
            )}
            {src.source && (
              <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)', marginBottom: '0.35rem' }}>
                Path: {src.source}
              </div>
            )}
            {src.content && (
              <div className="source-card-content">
                {src.content}
              </div>
            )}
          </div>
        )
      })}
    </aside>
  )
}
