export function formatted(value, unit, signal = null, exact = null) {
  if (value === null || value === undefined) return "待复核";
  const number = exact ?? String(value);
  return `${number}${unit ? " " + unit : ""}${signal ? " " + signal : ""}`;
}
