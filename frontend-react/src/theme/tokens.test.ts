import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { brand, fontFamily, radius } from "./tokens";

/**
 * 防止 CSS 与 AntD 两处令牌再次漂移：
 * tokens.css 是视觉真值，tokens.ts 是 AntD 的取值来源，这里做交叉校验。
 */
const css = fs.readFileSync(path.join(process.cwd(), "styles", "tokens.css"), "utf8");

function cssVar(name: string): string {
  const match = css.match(new RegExp(`${name}:\\s*([^;]+);`));
  return (match?.[1] ?? "").replace(/\s+/g, " ").trim();
}

const normalize = (value: string) => value.replace(/\s+/g, " ").trim();

describe("design tokens", () => {
  it("品牌主色与 tokens.css 一致", () => {
    expect(cssVar("--color-brand-solid")).toBe(brand[600]);
    expect(cssVar("--color-brand-solid-hover")).toBe(brand[700]);
  });

  it("圆角档位与 tokens.css 一致", () => {
    expect(cssVar("--radius-md")).toBe(`${radius.md}px`);
    expect(cssVar("--radius-lg")).toBe(`${radius.lg}px`);
    expect(cssVar("--radius-xl")).toBe(`${radius.xl}px`);
  });

  it("字体栈与 tokens.css 一致", () => {
    expect(normalize(cssVar("--font-sans"))).toBe(normalize(fontFamily));
  });

  it("不再声明未加载的字体", () => {
    const sans = normalize(cssVar("--font-sans"));
    expect(sans).not.toMatch(/Josefin Sans/);
    expect(sans).not.toMatch(/Times New Roman/);
    expect(sans).toMatch(/PingFang SC/);
  });
});
