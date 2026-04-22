/**
 * Minimal SSE-over-fetch parser. EventSource doesn't support POST + JSON body,
 * so we read a streaming response and emit `event` / `data` pairs to a handler.
 */

export interface SSEEvent {
  event: string
  data: any
}

export interface SSEStreamOptions {
  url: string
  body?: any
  headers?: Record<string, string>
  signal?: AbortSignal
  onEvent: (evt: SSEEvent) => void
  onError?: (err: any) => void
  onDone?: () => void
}

function safeParse(raw: string): any {
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return raw
  }
}

export async function streamSSE(opts: SSEStreamOptions): Promise<void> {
  const { url, body, headers, signal, onEvent, onError, onDone } = opts
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...(headers || {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal,
    })

    if (!resp.ok || !resp.body) {
      const text = await resp.text().catch(() => '')
      throw new Error(text || `HTTP ${resp.status}`)
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // SSE messages are separated by a blank line ("\n\n").
      let idx
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const chunk = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)

        let event = 'message'
        const dataLines: string[] = []
        for (const line of chunk.split('\n')) {
          if (!line || line.startsWith(':')) continue
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
        }
        if (dataLines.length === 0) continue
        const data = safeParse(dataLines.join('\n'))
        onEvent({ event, data })
      }
    }
    onDone?.()
  } catch (err) {
    if ((err as any)?.name === 'AbortError') return
    onError?.(err)
  }
}
