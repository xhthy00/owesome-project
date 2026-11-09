import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  apiRequest: vi.fn(),
  streamSSE: vi.fn()
}));

vi.mock("@/api/client", () => ({
  getApiBaseUrl: () => "/api/v1",
  apiRequest: mocks.apiRequest
}));
vi.mock("@/auth/session", () => ({
  getAccessToken: () => null
}));
vi.mock("@/utils/sse", () => ({
  streamSSE: mocks.streamSSE
}));

import { sendMessageStream, syncWorkspaceOid } from "@/api/adapter/chatAdapter";

describe("chatAdapter", () => {
  it("passes recoverable interrupt state through temporary metadata failures", async () => {
    const onError = vi.fn();
    mocks.streamSSE.mockImplementationOnce(async ({ onEvent }) => {
      await onEvent({ event: "error", data: { error: "目录暂时不可用", preserve_interrupt: true } });
      return { aborted: false };
    });
    await sendMessageStream({ question: "继续", datasource_id: 1 }, { onError });
    expect(onError).toHaveBeenCalledWith("目录暂时不可用", { preserveInterrupt: true });
  });
  it("awaits the record synchronization callback and ignores cancelled events", async () => {
    const onDone = vi.fn().mockResolvedValue(undefined);
    const onSummary = vi.fn();
    const controller = new AbortController();
    mocks.streamSSE.mockImplementationOnce(async ({ onEvent, requireDoneEvent }) => {
      expect(requireDoneEvent).toBe(true);
      await onEvent({ event: "done", data: { record_id: 1 } });
      controller.abort();
      await onEvent({ event: "summary", data: { content: "旧内容" } });
      return { aborted: true };
    });
    await sendMessageStream({ question: "继续", datasource_id: 1 }, { onDone, onSummary }, controller.signal);
    expect(onDone).toHaveBeenCalledWith(1);
    expect(onSummary).not.toHaveBeenCalled();
  });
  it("preserves current context independently from accumulated token usage", async () => {
    const onUsage = vi.fn();
    mocks.streamSSE.mockImplementationOnce(async ({ onEvent }) => {
      onEvent({ event: "usage", data: {
        total_tokens: 50000, context_tokens: 2000, context_limit: 100000,
        context_model: "test", context_key: "selection", context_estimated: false
      } });
      return { aborted: false };
    });
    await sendMessageStream({ question: "继续", datasource_id: 1 }, { onUsage });
    expect(onUsage).toHaveBeenCalledWith(expect.objectContaining({
      total_tokens: 50000, context_tokens: 2000, context_limit: 100000,
      context_model: "test", context_key: "selection", context_estimated: false
    }));
  });
  beforeEach(() => {
    mocks.apiRequest.mockReset();
    mocks.streamSSE.mockReset();
    window.localStorage.clear();
  });

  it("syncWorkspaceOid 仅在 oid 变化时写入并广播", () => {
    const seen: number[] = [];
    const onWs = (event: Event) => {
      seen.push(Number((event as CustomEvent<{ oid?: number }>).detail?.oid));
    };
    window.addEventListener("workspace:changed", onWs);
    try {
      window.localStorage.setItem("frontend_react_workspace_oid", "1");
      syncWorkspaceOid(1);
      syncWorkspaceOid(2);
      syncWorkspaceOid(2);
      expect(window.localStorage.getItem("frontend_react_workspace_oid")).toBe("2");
      expect(seen).toEqual([2]);
    } finally {
      window.removeEventListener("workspace:changed", onWs);
    }
  });

  it("传递 DeepAgents 追问标识并在补充回答中发送该标识", async () => {
    const onClarify = vi.fn();
    mocks.streamSSE.mockImplementationOnce(async ({ onEvent }) => {
      onEvent({
        event: "clarify",
        data: { interrupt_id: "interrupt-1", question_id: "interrupt-1", prompt: "请选择考试",
          fields: [{ label: "考试", kind: "single", options: ["期中"], required: true }] }
      });
      return { aborted: false };
    });

    await sendMessageStream({ question: "分析成绩", datasource_id: 1 }, { onClarify });
    expect(onClarify).toHaveBeenCalledWith(expect.objectContaining({
      interrupt_id: "interrupt-1",
      question_id: "interrupt-1", prompt: "请选择考试", status: "pending",
      fields: [{ label: "考试", kind: "single", options: ["期中"], required: true }]
    }));

    const reply = { question: "期中考试", datasource_id: 1, resume_interrupt_id: "interrupt-1" };
    await sendMessageStream(reply, {});
    expect(mocks.streamSSE).toHaveBeenLastCalledWith(expect.objectContaining({ body: reply }));
  });

  it("传递追问的已回复与已结束事件", async () => {
    const onHumanResolved = vi.fn();
    mocks.streamSSE.mockImplementationOnce(async ({ onEvent }) => {
      onEvent({ event: "human.answered", data: { interrupt_id: "ask-1", answer: "期中" } });
      onEvent({ event: "human.cancelled", data: { interrupt_id: "ask-2" } });
      return { aborted: false };
    });
    await sendMessageStream({ question: "期中", datasource_id: 1 }, { onHumanResolved });
    expect(onHumanResolved.mock.calls.map(([payload]) => payload)).toEqual([
      { interrupt_id: "ask-1", status: "answered", answer: "期中" },
      { interrupt_id: "ask-2", status: "cancelled", answer: undefined },
    ]);
  });
});
