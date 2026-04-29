import { createContext, useMemo, useState } from "react";

type ThemeMode = "dark" | "light";

type DialogInfo = {
  chat_scene: string;
  app_code: string;
};

type ChatContextType = {
  mode: ThemeMode;
  isMenuExpand: boolean;
  isContract: boolean;
  setMode: (mode: ThemeMode) => void;
  setIsMenuExpand: (value: boolean) => void;
  setIsContract: (value: boolean) => void;
  currentDialogInfo: DialogInfo;
  setCurrentDialogInfo: (info: DialogInfo) => void;
};

export const ChatContext = createContext<ChatContextType>({
  mode: "light",
  isMenuExpand: true,
  isContract: false,
  setMode: () => {},
  setIsMenuExpand: () => {},
  setIsContract: () => {},
  currentDialogInfo: {
    chat_scene: "",
    app_code: ""
  },
  setCurrentDialogInfo: () => {}
});

export function ChatContextProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>("light");
  const [isMenuExpand, setIsMenuExpand] = useState(true);
  const [isContract, setIsContract] = useState(false);
  const [currentDialogInfo, setCurrentDialogInfo] = useState<DialogInfo>({
    chat_scene: "",
    app_code: ""
  });

  const value = useMemo(
    () => ({
      mode,
      isMenuExpand,
      isContract,
      setMode,
      setIsMenuExpand,
      setIsContract,
      currentDialogInfo,
      setCurrentDialogInfo
    }),
    [mode, isMenuExpand, isContract, currentDialogInfo]
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}
