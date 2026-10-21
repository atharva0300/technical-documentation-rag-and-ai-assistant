import React, { useState, useEffect } from 'react'
import SourceDrawer, { SourceItem } from './components/SourceDrawer'
import AgentTracePanel from './components/AgentTracePanel'
import DocumentUploadModal from './components/DocumentUploadModal'

interface MessageItem {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources?: SourceItem[]
  trace?: any
  verificationStatus?: string
}

interface DocItem {
  document_id: string
  title: string
  chunks_count: number
}

const PROXY_BASE = '/api/proxy'

export default function App() {
  const [messages, setMessages] = useState<MessageItem[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content: 'Hello! I am your **Engineering AI Assistant**. Ask questions about indexed technical documentation, APIs, Kubernetes, Docker, or PostgreSQL.'
    }
  ])
  const [input, setInput] = useState('')
  const [retrievalMode, setRetrievalMode] = useState<string>('hybrid_rerank')
  const [activeSources, setActiveSources] = useState<SourceItem[]>([])
  const [activeSourceId, setActiveSourceId] = useState<number | null>(null)
  const [latestTrace, setLatestTrace] = useState<any>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [conversationId, setConversationId] = useState<string>('conv_' + Math.floor(Math.random() * 10000))
  const [documents, setDocuments] = useState<DocItem[]>([])

  const fetchDocuments = async () => {
    try {
      const res = await fetch(`${PROXY_BASE}/api/v2/documents`)
      if (res.ok) {
        const data = await res.json()
        setDocuments(data)
      }
    } catch (e) {
      console.warn('Failed to load documents list via proxy', e)
    }
  }

  useEffect(() => {
    fetchDocuments()
  }, [])

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!input.trim() || isLoading) return

    const userText = input.trim()
    const userMsgId = Date.now().toString()

    setMessages(prev => [...prev, { id: userMsgId, role: 'user', content: userText }])
    setInput('')
    setIsLoading(true)

    const asstMsgId = (Date.now() + 1).toString()
    setMessages(prev => [...prev, {
      id: asstMsgId,
      role: 'assistant',
      content: '',
      verificationStatus: 'VERIFYING...'
    }])

    const updateAsstMsg = (patch: Partial<MessageItem>) => {
      setMessages(prev => prev.map(m => (m.id === asstMsgId ? { ...m, ...patch } : m)))
    }

    try {
      const res = await fetch(`${PROXY_BASE}/api/v2/agent/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: userText,
          conversation_id: conversationId
        })
      })

      if (!res.ok || !res.body) {
        throw new Error(`Server returned status ${res.status}`)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let streamedText = ''

      // Minimal SSE parser: events are separated by a blank line, each carrying
      // an `event: <name>` line and a `data: <json>` line.
      const handleEvent = (eventName: string, dataStr: string) => {
        if (!dataStr) return
        const data = JSON.parse(dataStr)

        if (eventName === 'token') {
          streamedText += data.token
          updateAsstMsg({ content: streamedText, verificationStatus: 'VERIFYING...' })
        } else if (eventName === 'answer_replaced') {
          // The first attempt was rejected by the verifier and regenerated server-side —
          // discard everything streamed so far and show the final, verified answer instead.
          streamedText = data.answer
          updateAsstMsg({ content: streamedText, sources: data.sources })
        } else if (eventName === 'sources') {
          updateAsstMsg({ sources: data.sources })
          if (data.sources) setActiveSources(data.sources)
        } else if (eventName === 'verification') {
          updateAsstMsg({ verificationStatus: data.verification_status || 'VALID' })
        } else if (eventName === 'error') {
          updateAsstMsg({ content: `Error: ${data.error}`, verificationStatus: 'ERROR' })
        }
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        let sepIdx
        while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
          const rawEvent = buffer.slice(0, sepIdx)
          buffer = buffer.slice(sepIdx + 2)

          let eventName = 'message'
          let dataStr = ''
          for (const line of rawEvent.split('\n')) {
            if (line.startsWith('event:')) eventName = line.slice(6).trim()
            else if (line.startsWith('data:')) dataStr += line.slice(5).trim()
          }
          handleEvent(eventName, dataStr)
        }
      }
    } catch (err: any) {
      updateAsstMsg({ content: `Error: Unable to connect to backend proxy (${err.message}).`, verificationStatus: 'ERROR' })
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="app-container">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">⚡</div>
          <span>Engineering AI Platform</span>
        </div>

        <DocumentUploadModal onUploadSuccess={fetchDocuments} />

        <div className="strategy-select-container">
          <label className="label-title">Retrieval & Agent Mode</label>
          <select
            className="select-input"
            value={retrievalMode}
            onChange={(e) => setRetrievalMode(e.target.value)}
          >
            <option value="hybrid_rerank">Multi-Agent LangGraph + MCP (Recommended)</option>
            <option value="hybrid">Hybrid RRF Retrieval</option>
            <option value="dense">pgvector Dense Search</option>
            <option value="bm25">BM25 Technical Search</option>
          </select>
        </div>

        <AgentTracePanel trace={latestTrace} retrievalMode={retrievalMode} />

        {documents.length > 0 && (
          <div style={{ marginTop: '0.5rem', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
            <div className="label-title">Indexed Corpus Documents ({documents.length})</div>
            <div style={{ maxHeight: '120px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
              {documents.map((doc, idx) => (
                <div key={idx} style={{ padding: '0.35rem 0.5rem', background: 'var(--bg-card)', borderRadius: '4px', display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--text-main)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '180px' }}>
                    {doc.title || doc.document_id}
                  </span>
                  <span style={{ color: 'var(--accent-cyan)', fontFamily: 'var(--font-mono)' }}>{doc.chunks_count}c</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div style={{ marginTop: 'auto', fontSize: '0.75rem', color: 'var(--text-dim)', textAlign: 'center', paddingTop: '1rem' }}>
          LangGraph • MCP • pgvector • FastAPI • Express Proxy
        </div>
      </aside>

      {/* Main Chat Area */}
      <main className="chat-main">
        <header className="chat-header">
          <div className="header-title">Technical Documentation AI Workspace</div>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
            Thread: <span style={{ fontFamily: 'var(--font-mono)' }}>{conversationId}</span>
          </div>
        </header>

        <div className="chat-messages">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`message-bubble ${msg.role === 'user' ? 'message-user' : 'message-assistant'}`}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
                <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>{msg.role === 'user' ? 'You' : 'Engineering Assistant'}</span>
                {msg.verificationStatus && msg.role === 'assistant' && (
                  <span className={`status-tag status-${msg.verificationStatus.toLowerCase()}`} style={{ fontSize: '0.7rem', padding: '0.1rem 0.4rem', borderRadius: '4px', background: 'rgba(0,255,200,0.1)', color: '#00ffc8' }}>
                    ✓ {msg.verificationStatus}
                  </span>
                )}
              </div>
              <div style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</div>
              {msg.sources && msg.sources.length > 0 && (
                <div style={{ marginTop: '0.5rem', display: 'flex', flexWrap: 'wrap', gap: '0.25rem' }}>
                  {msg.sources.map((src, i) => (
                    <span
                      key={i}
                      className="citation-badge"
                      title={src.title || src.source}
                      onClick={() => {
                        setActiveSources(msg.sources || [])
                        setActiveSourceId(src.source_id || i + 1)
                      }}
                    >
                      {/* The section (e.g. "7.14 Registering a LoadTimeWeaver · p. 170") says
                          where in the docs a citation points; the document title is the tooltip. */}
                      [{src.source_id || i + 1}] {src.section || src.title || src.source}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="chat-input-area">
          <form className="input-form" onSubmit={handleSend}>
            <input
              type="text"
              className="chat-input"
              placeholder="Ask a technical question (e.g. Compare HNSW vs IVFFlat or explain --memory flag)..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={isLoading}
            />
            <button type="submit" className="send-btn" disabled={isLoading}>
              {isLoading ? 'Processing...' : 'Send Query'}
            </button>
          </form>
        </div>
      </main>

      <SourceDrawer sources={activeSources} activeSourceId={activeSourceId} />
    </div>
  )
}
