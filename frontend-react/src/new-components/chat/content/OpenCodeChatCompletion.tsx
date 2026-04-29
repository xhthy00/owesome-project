import { useMemo } from "react";
import { Message } from "@/hooks/useChat";
import OpenCodeSessionTurn from "./OpenCodeSessionTurn";

type Props = {
  messages: Message[];
  loading?: boolean;
};

type Turn = {
  id: string;
  userMessage: string;
  assistantMessage?: string;
};

export default function OpenCodeChatCompletion({ messages, loading }: Props) {
  const turns = useMemo<Turn[]>(() => {
    const grouped: Turn[] = [];
    let currentUser: Message | undefined;

    messages.forEach((msg) => {
      if (msg.role === "user") {
        currentUser = msg;
        return;
      }
      if (msg.role === "assistant") {
        grouped.push({
          id: msg.id,
          userMessage: currentUser?.content ?? "",
          assistantMessage: msg.content
        });
        currentUser = undefined;
      }
    });

    if (currentUser) {
      grouped.push({
        id: `${currentUser.id}-pending`,
        userMessage: currentUser.content,
        assistantMessage: ""
      });
    }
    return grouped;
  }, [messages]);

  return (
    <div className="mx-auto flex w-5/6 flex-col space-y-2 py-4">
      {turns.map((turn, index) => (
        <OpenCodeSessionTurn
          key={turn.id}
          userMessage={turn.userMessage}
          assistantMessage={turn.assistantMessage}
          isWorking={loading && index === turns.length - 1}
        />
      ))}
    </div>
  );
}
