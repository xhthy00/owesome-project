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
        data: { interrupt_id: "interrupt-1", prompt: "请选择考试" }
      });
      return { aborted: false };
    });

    await sendMessageStream({ question: "分析成绩", datasource_id: 1 }, { onClarify });
    expect(onClarify).toHaveBeenCalledWith(expect.objectContaining({
      interrupt_id: "interrupt-1",
      prompt: "请选择考试"
    }));

    const reply = { question: "期中考试", datasource_id: 1, resume_interrupt_id: "interrupt-1" };
    await sendMessageStream(reply, {});
    expect(mocks.streamSSE).toHaveBeenLastCalledWith(expect.objectContaining({ body: reply }));
  });
});
