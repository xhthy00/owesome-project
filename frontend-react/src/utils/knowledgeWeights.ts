export function suggestWeights(confidences: number[]): number[] {
  const count = Math.min(confidences.length, 3);
  if (count <= 0) return [];
  if (count === 1) return [1];
  if (count === 2) {
    const gap = (confidences[0] || 0) - (confidences[1] || 0);
    return gap < 0.1 ? [0.5, 0.5] : [0.6, 0.4];
  }
  return [0.5, 0.3, 0.2];
}

function roundTenth(value: number): number {
  return Math.round(value * 10) / 10;
}

/** 锁定用户改过的一项，其余按原比例分掉剩余权重，结果仍是一位小数且合计为 1。 */
export function adjustWeight(current: number[], index: number, next: number): number[] {
  const count = current.length;
  if (count <= 1) return [1];
  const minOthers = (count - 1) * 0.1;
  const clamped = roundTenth(Math.min(Math.max(next, 0.1), 1 - minOthers));
  const rest = roundTenth(1 - clamped);
  const otherSum = current.reduce((sum, value, item) => (item === index ? sum : sum + value), 0) || 1;
  const result = current.slice();
  result[index] = clamped;
  let assigned = 0;
  const others = current.map((_, item) => item).filter((item) => item !== index);
  others.forEach((item, position) => {
    if (position === others.length - 1) {
      result[item] = roundTenth(rest - assigned);
      return;
    }
    const share = Math.max(0.1, roundTenth((current[item] / otherSum) * rest));
    result[item] = share;
    assigned = roundTenth(assigned + share);
  });
  return result;
}
