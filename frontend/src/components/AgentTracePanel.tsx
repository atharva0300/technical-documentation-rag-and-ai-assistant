import React from 'react'

interface AgentTraceProps {
  trace: {
    step_count?: number
    tool_calls_count?: number
    query_type?: string
  } | null
  retrievalMode: string
}

export default function AgentTracePanel({ trace, retrievalMode }: AgentTraceProps) {
  return (
    <div style={{ padding: '0.75rem 1rem', background: 'var(--bg-tertiary)', borderRadius: '8px', marginBottom: '1rem', fontSize: '0.8rem' }}>
      <div style={{ fontWeight: 600, color: 'var(--accent-cyan)', marginBottom: '0.35rem' }}>
        ⚡ LangGraph Agent Trace
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', color: 'var(--text-muted)' }}>
        <div>Mode: <span style={{ color: 'var(--text-main)', fontFamily: 'var(--font-mono)' }}>{retrievalMode}</span></div>
        <div>Query Type: <span style={{ color: 'var(--text-main)', fontFamily: 'var(--font-mono)' }}>{trace?.query_type || 'search'}</span></div>
        <div>Steps: <span style={{ color: 'var(--accent-emerald)', fontFamily: 'var(--font-mono)' }}>{trace?.step_count || 1}</span></div>
        <div>Tool Calls: <span style={{ color: 'var(--accent-purple)', fontFamily: 'var(--font-mono)' }}>{trace?.tool_calls_count || 0}</span></div>
      </div>
    </div>
  )
}
