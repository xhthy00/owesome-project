import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { useChat } from "./useChat";
import type { ClarifyPayload, ConversationDetail } from "@/api/adapter/chatAdapter";

const mocks = vi.hoisted(() => ({ detail: vi.fn(), stream: vi.fn(), create: vi.fn() }));
vi.mock("@/api/adapter/chatAdapter", () => ({
  getConversationDetail: mocks.detail, sendMessageStream: mocks.stream, createConversation: mocks.create,
  syncWorkspaceOid: vi.fn(), updateReportReview: vi.fn(), replaceRecordReports: vi.fn(),
}));

const form: ClarifyPayload & { clarify: true } = {
  clarify: true, interrupt_id: "ask-1", question_id: "ask-1", prompt: "确认考试", status: "pending",
  fields: [{ label: "考试", kind: "single", options: ["期中"], required: true }],
};
const detail: ConversationDetail = { id: 9, datasource_id: 4, title: "分析考试", records: [
  { id: 1, question: "分析考试", summary: "确认考试", exec_result: form },
] };

beforeEach(() => {
  vi.clearAllMocks();
  mocks.detail.mockResolvedValue(detail);
  mocks.stream.mockImplementation(async (_payload, handlers) => { await handlers.onDone(2); return { aborted: false }; });
});

it("restores structured pending forms and resumes the exact interrupt after refresh", async () => {
  const { result } = renderHook(() => useChat());
  await act(async () => { await result.current.loadConversation(9); });
  expect(result.current.clarifyByRunId["record-1"].fields).toEqual(form.fields);
  await act(async () => { await result.current.send("1. 考试：期中"); });
  expect(mocks.stream).toHaveBeenCalledWith(expect.objectContaining({
    question: "1. 考试：期中", conversation_id: 9, datasource_id: 4, resume_interrupt_id: "ask-1",
  }), expect.anything(), expect.anything());
  expect(result.current.clarifyByRunId["record-1"]).toMatchObject({ status: "answered", answer: "1. 考试：期中" });
});

it("keeps successive questions separate when a reply triggers another clarification", async () => {
  const next = { ...form, interrupt_id: "ask-2", question_id: "ask-2", prompt: "确认科目" };
  mocks.stream.mockImplementationOnce(async (_payload, handlers) => {
    handlers.onClarify(next);
    await handlers.onDone(2);
    return { aborted: false };
  });
  const { result } = renderHook(() => useChat());
  await act(async () => { await result.current.loadConversation(9); });
  await act(async () => { await result.current.send("期中", { resumeInterruptId: "ask-1" }); });
  const questions = Object.values(result.current.clarifyByRunId);
  expect(questions.find((q) => q.interrupt_id === "ask-1")?.status).toBe("answered");
  expect(questions.find((q) => q.interrupt_id === "ask-2")?.status).toBe("pending");
  await act(async () => { await result.current.send("数学"); });
  expect(mocks.stream).toHaveBeenLastCalledWith(expect.objectContaining({ resume_interrupt_id: "ask-2" }), expect.anything(), expect.anything());
});

it("does not consume a clarification when the server rejects the reply", async () => {
  mocks.stream.mockImplementationOnce(async (_payload, handlers) => {
    handlers.onError("当前会话已有查询正在执行", { preserveInterrupt: true });
    await handlers.onDone(0);
    return { aborted: false };
  });
  const { result } = renderHook(() => useChat());
  await act(async () => { await result.current.loadConversation(9); });
  await act(async () => { await result.current.send("期中"); });
  expect(result.current.clarifyByRunId["record-1"].status).toBe("pending");
  await act(async () => { await result.current.send("期末"); });
  expect(mocks.stream).toHaveBeenLastCalledWith(expect.objectContaining({ resume_interrupt_id: "ask-1" }), expect.anything(), expect.anything());
});

it.each(["answered", "cancelled"] as const)("honours %s lifecycle events even when no display record was saved", async (status) => {
  mocks.stream.mockImplementationOnce(async (_payload, handlers) => {
    handlers.onHumanResolved({ interrupt_id: "ask-1", status, answer: status === "answered" ? "期中" : undefined });
    await handlers.onDone(0);
    return { aborted: false };
  });
  const { result } = renderHook(() => useChat());
  await act(async () => { await result.current.loadConversation(9); });
  await act(async () => { await result.current.send("期中"); });
  expect(result.current.clarifyByRunId["record-1"].status).toBe(status);
  await act(async () => { await result.current.send("继续"); });
  expect(mocks.stream).toHaveBeenLastCalledWith(expect.objectContaining({ resume_interrupt_id: undefined }), expect.anything(), expect.anything());
});

it("restores answered and cancelled history without reopening either question", async () => {
  mocks.detail.mockResolvedValue({ ...detail, records: [
    ...detail.records,
    { id: 2, question: "期中", turn_type: "retry", exec_result: { ...form, interrupt_id: "ask-2", human_reply_to: "ask-1", human_answer: "期中" } },
    { id: 3, question: "换个问题，查总人数", exec_result: { human_cancelled_to: "ask-2" } },
  ] });
  const { result } = renderHook(() => useChat());
  await act(async () => { await result.current.loadConversation(9); });
  expect(result.current.clarifyByRunId["record-1"]).toMatchObject({ status: "answered", answer: "期中" });
  expect(result.current.clarifyByRunId["record-2"].status).toBe("cancelled");
  await act(async () => { await result.current.send("查人数"); });
  expect(mocks.stream).toHaveBeenLastCalledWith(expect.objectContaining({ resume_interrupt_id: undefined }), expect.anything(), expect.anything());
});

it("does not reopen legacy waits followed by a standalone turn without lifecycle metadata", async () => {
  mocks.detail.mockResolvedValue({ ...detail, records: [
    ...detail.records,
    { id: 2, question: "换个问题，查总人数", turn_type: "standalone", summary: "查询完成" },
  ] });
  const { result } = renderHook(() => useChat());
  await act(async () => { await result.current.loadConversation(9); });
  expect(result.current.clarifyByRunId["record-1"].status).toBe("cancelled");
});
