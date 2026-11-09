import { expect, it } from "vitest";
import { humanQuestionText } from "../../../frontend/src/utils/humanQuestions";

it("preserves every field and option in the legacy Vue text fallback", () => {
  expect(humanQuestionText({ prompt: "请确认", fields: [
    { label: "考试", required: true, options: ["期中", "期末"] },
    { label: "偏好" },
  ] })).toBe("请确认\n1. 考试（必填）：期中 / 期末\n2. 偏好（选填）");
  expect(humanQuestionText({ options: { exam: ["期中"] } })).toContain("期中，也可自行填写");
});
