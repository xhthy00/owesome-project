// 生成 RFC4122 v4 UUID。
//
// crypto.randomUUID() 仅在「安全上下文」(https 或 http://localhost)下可用。
// 通过 http + 局域网 IP 访问服务器时浏览器会屏蔽该 API，
// 抛 "crypto.randomUUID is not a function"，导致对话提交即崩。
// 故优先用 randomUUID，不可用则降级到 getRandomValues 手拼 v4；二者皆无再用 Math.random 兜底。
export function genUUID(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // getRandomValues 在非安全上下文下仍可用，是可靠的降级路径。
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40; // version 4
    b[8] = (b[8] & 0x3f) | 0x80; // variant 10
    const h = (n: number) => n.toString(16).padStart(2, "0");
    return (
      h(b[0]) + h(b[1]) + h(b[2]) + h(b[3]) +
      "-" + h(b[4]) + h(b[5]) +
      "-" + h(b[6]) + h(b[7]) +
      "-" + h(b[8]) + h(b[9]) +
      "-" + h(b[10]) + h(b[11]) + h(b[12]) + h(b[13]) + h(b[14]) + h(b[15])
    );
  }
  // 极老环境兜底，正常不会走到。
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
