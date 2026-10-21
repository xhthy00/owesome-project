import { describe, expect, it } from "vitest";
import { adjustWeight, suggestWeights } from "./knowledgeWeights";

describe("suggestWeights", () => {
  it("按置信度差距分配一位小数权重", () => {
    expect(suggestWeights([0.99])).toEqual([1]);
    expect(suggestWeights([0.92, 0.9])).toEqual([0.5, 0.5]);
    expect(suggestWeights([0.95, 0.7])).toEqual([0.6, 0.4]);
    expect(suggestWeights([0.9, 0.8, 0.4])).toEqual([0.5, 0.3, 0.2]);
  });
});

describe("adjustWeight", () => {
  it("改一项后其余权重补齐到 1，且只有一位小数", () => {
    expect(adjustWeight([0.5, 0.3, 0.2], 0, 0.7)).toEqual([0.7, 0.2, 0.1]);
    expect(adjustWeight([0.6, 0.4], 0, 0.8)).toEqual([0.8, 0.2]);
  });
});