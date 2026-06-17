// Port of src/zhnum.py — Chinese numeral utilities
const DIG = { 零:0,一:1,二:2,两:2,三:3,四:4,五:5,六:6,七:7,八:8,九:9 };
const UNIT = { 十:10,百:100,千:1000 };
const DIGITS = "零一二三四五六七八九";

export function cjkToInt(s) {
  let total = 0, num = 0;
  for (const ch of s) {
    if (ch in DIG) { num = DIG[ch]; }
    else if (ch in UNIT) { total += (num || 1) * UNIT[ch]; num = 0; }
  }
  return total + num;
}

export function intToCjk(n) {
  if (n <= 0) return String(n);
  const parts = [];
  for (const [uv, uc] of [[1000,'千'],[100,'百'],[10,'十']]) {
    const d = Math.floor(n / uv);
    if (d) {
      if (!(uv === 10 && d === 1 && !parts.length)) parts.push(DIGITS[d]);
      parts.push(uc);
      n -= d * uv;
    } else if (parts.length && n) {
      if (parts[parts.length - 1] !== '零') parts.push('零');
    }
  }
  if (n) parts.push(DIGITS[n]);
  return parts.join('');
}

export function normClause(s) {
  if (s == null) return null;
  s = String(s).trim();
  if (!s) return null;
  if (/^\d+$/.test(s)) return intToCjk(parseInt(s, 10));
  return s;
}
