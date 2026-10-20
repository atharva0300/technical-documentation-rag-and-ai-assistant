import React, { useState } from 'react'
import { Upload, FileText, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react'

interface DocumentUploadModalProps {
  onUploadSuccess?: () => void
}

// Routed through the Express server-side proxy (/api/proxy) rather than calling the backend
// directly: a direct call bypasses the proxy's x-api-key header entirely, which is exactly
// how this endpoint ended up with no auth check at all (see the debloat audit's Mistake #2).
const PROXY_BASE = '/api/proxy'

export default function DocumentUploadModal({ onUploadSuccess }: DocumentUploadModalProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [statusMessage, setStatusMessage] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(null)

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setSelectedFile(e.target.files[0])
      setStatusMessage(null)
    }
  }

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedFile) return

    setUploading(true)
    setStatusMessage({ type: 'info', text: `Ingesting and indexing ${selectedFile.name}...` })

    const formData = new FormData()
    formData.append('file', selectedFile)

    try {
      const res = await fetch(`${PROXY_BASE}/api/v2/documents/upload`, {
        method: 'POST',
        body: formData,
      })

      const rawText = await res.text()
      let data: any = {}
      try {
        data = JSON.parse(rawText)
      } catch {
        throw new Error(`Server returned error status (${res.status}): ${rawText.slice(0, 150) || res.statusText}`)
      }

      if (!res.ok) {
        throw new Error(data.detail || data.message || `Upload failed with status ${res.status}`)
      }

      setStatusMessage({
        type: 'success',
        text: `Successfully ingested "${selectedFile.name}"! (${data.chunks_created || 0} chunks indexed)`
      })
      setSelectedFile(null)
      if (onUploadSuccess) onUploadSuccess()

    } catch (err: any) {
      setStatusMessage({
        type: 'error',
        text: err.message || 'Failed to upload document.'
      })
    } finally {
      setUploading(false)
    }
  }

  return (
    <div style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border-color)', borderRadius: '10px', padding: '1rem', marginBottom: '1.25rem' }}>
      <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-main)', marginBottom: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <Upload size={16} color="var(--accent-cyan)" />
        Upload Technical Docs (PDF / MD / TXT)
      </div>

      <form onSubmit={handleUpload} style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        <label
          style={{
            border: '2px dashed var(--border-color)',
            borderRadius: '8px',
            padding: '0.75rem',
            textAlign: 'center',
            cursor: 'pointer',
            background: 'rgba(0,0,0,0.2)',
            fontSize: '0.8rem',
            color: 'var(--text-muted)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '0.5rem'
          }}
        >
          <FileText size={16} />
          {selectedFile ? selectedFile.name : 'Select PDF (.pdf), Markdown (.md), or Text (.txt)'}
          <input
            type="file"
            accept=".pdf,.md,.txt,.markdown"
            onChange={handleFileChange}
            style={{ display: 'none' }}
          />
        </label>

        {selectedFile && (
          <button
            type="submit"
            disabled={uploading}
            style={{
              background: 'linear-gradient(135deg, var(--accent-cyan), var(--accent-blue))',
              color: 'white',
              border: 'none',
              padding: '0.5rem',
              borderRadius: '6px',
              fontWeight: 600,
              fontSize: '0.8rem',
              cursor: uploading ? 'not-allowed' : 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '0.5rem'
            }}
          >
            {uploading ? (
              <>
                <Loader2 size={14} className="spin" />
                Parsing PDF & Embedding...
              </>
            ) : (
              'Upload & Index Document'
            )}
          </button>
        )}
      </form>

      {statusMessage && (
        <div
          style={{
            marginTop: '0.75rem',
            fontSize: '0.78rem',
            padding: '0.5rem 0.75rem',
            borderRadius: '6px',
            display: 'flex',
            alignItems: 'center',
            gap: '0.4rem',
            background: statusMessage.type === 'success' ? 'rgba(16, 185, 129, 0.15)' : statusMessage.type === 'error' ? 'rgba(239, 68, 68, 0.15)' : 'rgba(59, 130, 246, 0.15)',
            color: statusMessage.type === 'success' ? 'var(--accent-emerald)' : statusMessage.type === 'error' ? '#ef4444' : 'var(--accent-blue)',
            border: `1px solid ${statusMessage.type === 'success' ? 'rgba(16, 185, 129, 0.3)' : statusMessage.type === 'error' ? 'rgba(239, 68, 68, 0.3)' : 'rgba(59, 130, 246, 0.3)'}`
          }}
        >
          {statusMessage.type === 'success' && <CheckCircle2 size={14} />}
          {statusMessage.type === 'error' && <AlertCircle size={14} />}
          <span>{statusMessage.text}</span>
        </div>
      )}
    </div>
  )
}
