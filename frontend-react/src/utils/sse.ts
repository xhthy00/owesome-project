export interface SSEEvent {
  event: string;
  data: unknown;
}

export interface SSEStreamOptions {
  url: string;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  onEvent: (evt: SSEEvent) => void;
  onError?: (err: unknown) => void;
  onDone?: () => void;
}

function safeParse(raw: string): unknown {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

export async function streamSSE(opts: SSEStreamOptions): Promise<void> {
  const { url, body, headers, signal, onEvent, onError, onDone } = opts;
  const emitChunk = (chunk: string) => {
    if (!chunk.trim()) return;
    let event = "message";
    const dataLines: string[] = [];
    for (const line of chunk.split(/\r?\n/)) {
      if (!line || line.startsWith(":")) continue;
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (dataLines.length === 0) return;
    onEvent({ event, data: safeParse(dataLines.join("\n")) });
  };

  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(headers ?? {})
      },
      body: body ? JSON.stringify(body) : undefined,
      signal
    });

    if (!resp.ok || !resp.body) {
      const text = await resp.text().catch(() => "");
      throw new Error(text || `HTTP ${resp.status}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const parts = buffer.split(/\r?\n\r?\n/);
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        emitChunk(part);
      }
    }
    // Flush any remaining SSE frame in buffer.
    if (buffer.trim()) {
      emitChunk(buffer);
    }
    onDone?.();
  } catch (err) {
    if ((err as { name?: string })?.name === "AbortError") return;
    onError?.(err);
  }
}
