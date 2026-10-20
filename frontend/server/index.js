import express from 'express'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const PORT = process.env.PORT || (process.env.NODE_ENV === 'production' ? 3000 : 3001)
const API_BACKEND_URL = process.env.API_BACKEND_URL || 'http://localhost:8000'
const BACKEND_API_KEY = process.env.API_KEY || 'dev-api-key'

const app = express()

// Replaces the old Next.js route handler at src/app/api/proxy/[...path]/route.ts: a
// server-side proxy so the backend API key is attached here and never reaches the
// browser bundle. Streams the request body straight through (so multipart uploads and
// JSON bodies both work with no re-parsing) and streams the response straight back,
// which is what lets Server-Sent Events pass through untouched.
app.all('/api/proxy/*', async (req, res) => {
  const targetPath = req.params[0]
  const queryIndex = req.url.indexOf('?')
  const search = queryIndex !== -1 ? req.url.slice(queryIndex) : ''
  const targetUrl = `${API_BACKEND_URL}/${targetPath}${search}`

  const headers = { 'x-api-key': BACKEND_API_KEY }
  if (req.headers['content-type']) headers['content-type'] = req.headers['content-type']
  if (req.headers['accept']) headers['accept'] = req.headers['accept']

  const init = { method: req.method, headers }
  if (!['GET', 'HEAD', 'DELETE'].includes(req.method)) {
    init.body = req
    init.duplex = 'half'
  }

  try {
    const backendRes = await fetch(targetUrl, init)
    const contentType = backendRes.headers.get('content-type') || ''

    if (contentType.includes('text/event-stream')) {
      res.status(backendRes.status)
      res.setHeader('Content-Type', 'text/event-stream')
      res.setHeader('Cache-Control', 'no-cache')
      res.setHeader('Connection', 'keep-alive')
      res.flushHeaders?.()
      for await (const chunk of backendRes.body) {
        res.write(chunk)
      }
      res.end()
      return
    }

    if (backendRes.status === 204) {
      res.status(204).end()
      return
    }

    const buf = Buffer.from(await backendRes.arrayBuffer())
    res.status(backendRes.status)
    res.setHeader('Content-Type', contentType || 'application/json')
    res.end(buf)
  } catch (err) {
    res.status(502).json({ error: 'Failed to connect to backend service' })
  }
})

// In production this same process also serves the Vite-built static SPA. In dev, the
// Vite dev server (a separate process, started alongside this one by `npm run dev`)
// serves the SPA and proxies /api/proxy/* here instead.
if (process.env.NODE_ENV === 'production') {
  const distDir = path.join(__dirname, '..', 'dist')
  app.use(express.static(distDir))
  app.get('*', (req, res) => {
    res.sendFile(path.join(distDir, 'index.html'))
  })
}

app.listen(PORT, () => {
  console.log(`Frontend proxy server listening on :${PORT} (backend: ${API_BACKEND_URL})`)
})
