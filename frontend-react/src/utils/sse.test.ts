import { afterEach, expect, it, vi } from "vitest";
import { streamSSE } from "./sse";
import { streamSSE as legacyStreamSSE } from "../../../frontend/src/utils/sse";

afterEach(() => vi.unstubAllGlobals());

function response(parts: string[]) {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      parts.forEach((part) => controller.enqueue(encoder.encode(part)));
      controller.close();
    }
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, body }));
}

it("waits for asynchronous completion work before resolving the stream", async () => {
  response(['event: done\r\ndata: {"record_id":9}\r\n\r\n']);
  let finish!: () => void;
  const completed = new Promise<void>((resolve) => { finish = resolve; });
  const seen = vi.fn();
  const stream = streamSSE({ url: "/chat", requireDoneEvent: true,
    onEvent: async () => { await completed; seen(); } });
  let settled = false;
  void stream.then(() => { settled = true; });
  await Promise.resolve();
  expect(settled).toBe(false);
  finish();
  await stream;
  expect(seen).toHaveBeenCalledOnce();
  expect(settled).toBe(true);
});

it("reports EOF without a terminal event instead of accepting partial output as complete", async () => {
  response(['event: answer_delta\ndata: {"text":"部分结论"}\n\n']);
  const onError = vi.fn();
  const onDone = vi.fn();
  await streamSSE({ url: "/chat", requireDoneEvent: true, onEvent: vi.fn(), onError, onDone });
  expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringContaining("提前结束") }));
  expect(onDone).not.toHaveBeenCalled();
});

it("parses frames split across CRLF boundaries and a final frame without a blank line", async () => {
  response([': heartbeat\r\n\r\nevent: summary\r\ndata: {"content":"结论"}\r',
    '\n\r\nevent: done\ndata: {"record_id":1}']);
  const onEvent = vi.fn();
  await streamSSE({ url: "/chat", requireDoneEvent: true, onEvent });
  expect(onEvent.mock.calls.map(([event]) => event.event)).toEqual(["summary", "done"]);
});

it("ignores buffered events after abort so an old request cannot overwrite a new chat", async () => {
  response(['event: summary\ndata: {"content":"旧内容"}\n\nevent: done\ndata: {}\n\n']);
  const controller = new AbortController();
  controller.abort();
  const onEvent = vi.fn();
  const result = await streamSSE({ url: "/chat", signal: controller.signal, onEvent });
  expect(result.aborted).toBe(true);
  expect(onEvent).not.toHaveBeenCalled();
});

it("keeps the Vue parser compatible with split frames and terminal completion", async () => {
  response(['event: summary\r\ndata: {"content":"结论"}\r', '\n\r\nevent: done\ndata: {}\n\n']);
  const onEvent = vi.fn();
  const onError = vi.fn();
  await legacyStreamSSE({ url: "/chat", requireDoneEvent: true, onEvent, onError });
  expect(onEvent.mock.calls.map(([event]) => event.event)).toEqual(["summary", "done"]);
  expect(onError).not.toHaveBeenCalled();
});

it("reports incomplete streams in the Vue parser too", async () => {
  response(['event: summary\ndata: {"content":"部分内容"}\n\n']);
  const onError = vi.fn();
  await legacyStreamSSE({ url: "/chat", requireDoneEvent: true, onEvent: vi.fn(), onError });
  expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringContaining("提前结束") }));
});
