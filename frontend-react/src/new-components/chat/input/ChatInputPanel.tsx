import { ArrowUpOutlined, LoadingOutlined } from "@ant-design/icons";
import { Button, Input, Spin } from "antd";
import classNames from "classnames";
import { useContext, useState } from "react";
import { ChatContentContext } from "@/new-components/chat/context";
import ToolsBar from "./ToolsBar";

export default function ChatInputPanel() {
  const { replyLoading, handleChat } = useContext(ChatContentContext);
  const [userInput, setUserInput] = useState("");
  const [isFocus, setIsFocus] = useState(false);
  const [isZhInput, setIsZhInput] = useState(false);

  const onSubmit = async () => {
    const value = userInput.trim();
    if (!value || replyLoading) return;
    setUserInput("");
    await handleChat(value);
  };

  return (
    <div className="dbgpt-ui-font flex w-full flex-col bg-transparent pb-5 pt-2">
      <div
        id="input-panel"
        className={`relative flex min-h-[132px] flex-1 flex-col rounded-2xl border border-[#eceff5] bg-white px-4 pb-3 pt-3 shadow-[0_2px_8px_rgba(15,23,42,0.04)] dark:border-[rgba(255,255,255,0.14)] dark:bg-[rgba(255,255,255,0.08)] ${
          isFocus ? "border-[#0c75fc]" : ""
        }`}
      >
        <Input.TextArea
          placeholder="向您的数据库提问，比如CSV，或生成报告..."
          className="dbgpt-input-font mt-0.5 h-[62px] w-full resize-none border-0 p-0 !text-[13px] !leading-6 text-[#1f2937] placeholder:!text-[12px] placeholder:!text-[#c0c4cc] focus:shadow-none dark:bg-transparent dark:text-[#e5e7eb]"
          value={userInput}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.shiftKey || isZhInput) return;
            e.preventDefault();
            onSubmit();
          }}
          onChange={(e) => setUserInput(e.target.value)}
          onFocus={() => setIsFocus(true)}
          onBlur={() => setIsFocus(false)}
          onCompositionStart={() => setIsZhInput(true)}
          onCompositionEnd={() => setIsZhInput(false)}
        />
        <ToolsBar />
        <Button
          type="text"
          className={classNames(
            "absolute bottom-2.5 right-2.5 flex h-7 w-7 items-center justify-center rounded-full border-0 text-[12px]",
            userInput.trim()
              ? "bg-[#1677ff] !text-white hover:!bg-[#4096ff]"
              : "bg-[#eef0f4] !text-[#a0a8b5] hover:!bg-[#e7eaf0] dark:bg-[#2a3040] dark:!text-[#717b91]",
            { "cursor-not-allowed": !userInput.trim() && !replyLoading }
          )}
          onClick={onSubmit}
        >
          {replyLoading ? (
            <Spin spinning indicator={<LoadingOutlined className="text-white" />} />
          ) : (
            <ArrowUpOutlined />
          )}
        </Button>
      </div>
    </div>
  );
}
