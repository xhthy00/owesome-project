import { createContext } from "react";

type ChatParam = { type: string; value?: string };

export type ChatContentContextType = {
  replyLoading: boolean;
  canAbort: boolean;
  handleChat: (text: string) => Promise<void>;
  stopReply: () => void;
  replayLast: () => void;
  clearHistory: () => void;
  appInfo: { param_need: ChatParam[] };
  temperatureValue: number;
  maxNewTokensValue: number;
  resourceValue: string;
  modelValue: string;
  modelList: string[];
  setTemperatureValue: (v: number) => void;
  setMaxNewTokensValue: (v: number) => void;
  setResourceValue: (v: string) => void;
  setModelValue: (v: string) => void;
};

export const ChatContentContext = createContext<ChatContentContextType>({
  replyLoading: false,
  canAbort: false,
  handleChat: async () => {},
  stopReply: () => {},
  replayLast: () => {},
  clearHistory: () => {},
  appInfo: { param_need: [] },
  temperatureValue: 0.6,
  maxNewTokensValue: 4000,
  resourceValue: "",
  modelValue: "dbgpt-pro",
  modelList: [],
  setTemperatureValue: () => {},
  setMaxNewTokensValue: () => {},
  setResourceValue: () => {},
  setModelValue: () => {}
});
