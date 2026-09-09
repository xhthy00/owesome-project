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

import {
  rejectQuestion,
  replyQuestion,
  sendMessageStream,
  syncWorkspaceOid
} from "@/api/adapter/chatAdapter";

describe("chatAdapter HITL question", () => {
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

  it("分发 question.asked 与 question.rejected SSE 事件", async () => {
    const onQuestionAsked = vi.fn();
    const onQuestionRejected = vi.fn();
    mocks.streamSSE.mockImplementation(async ({
      onEvent
    }: {
      onEvent: (event: { event: string; data: Record<string, unknown> }) => void;
    }) => {
      onEvent({
        event: "question.asked",
        data: {
          request_id: "que_1",
          conv_id: "12",
          questions: [{ question: "选择口径", options: [], custom: true }]
        }
      });
      onEvent({
        event: "question.rejected",
        data: { request_id: "que_1", conv_id: "12" }
      });
      return { aborted: false };
    });

    await sendMessageStream(
      { question: "分析成绩", datasource_id: 1 },
      { onQuestionAsked, onQuestionRejected }
    );

    expect(onQuestionAsked).toHaveBeenCalledWith({
      request_id: "que_1",
      conv_id: "12",
      questions: [{ question: "选择口径", options: [], custom: true }]
    });
    expect(onQuestionRejected).toHaveBeenCalledWith({
      request_id: "que_1",
      conv_id: "12"
    });
  });

  it("调用 reply 与 reject REST API", async () => {
    mocks.apiRequest.mockResolvedValue({ request_id: "que_1" });

    await replyQuestion("que_1", [["年级"]]);
    await rejectQuestion("que_1");

    expect(mocks.apiRequest).toHaveBeenNthCalledWith(
      1,
      "/chat/question/que_1/reply",
      { method: "POST", body: JSON.stringify({ answers: [["年级"]] }) }
    );
    expect(mocks.apiRequest).toHaveBeenNthCalledWith(
      2,
      "/chat/question/que_1/reject",
      { method: "POST" }
    );
  });
});
