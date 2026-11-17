export const CONVERSATION_CHANGED_EVENT = "conversation:changed";

const TITLE_MAX = 32;

export function snippetConversationTitle(question: string): string {
  const text = (question || "").replace(/\s+/g, " ").trim();
  if (!text) return "新对话";
  return text.slice(0, TITLE_MAX);
}

export function notifyConversationChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(CONVERSATION_CHANGED_EVENT));
}
