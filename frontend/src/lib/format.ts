export const COURSE_COLORS: Record<string, string> = {
  blue: "#3e6fb0",
  green: "#3d8b5f",
  orange: "#c7742c",
  purple: "#7a5bb5",
  red: "#c2554d",
  teal: "#2b8a8a",
  brown: "#8a6a4f",
  pink: "#b8577e",
};

export const colorOf = (c?: string | null) => COURSE_COLORS[c || "blue"] || COURSE_COLORS.blue;

const WEEK = ["日", "一", "二", "三", "四", "五", "六"];

export function todayLabel(d = new Date()) {
  return `${d.getMonth() + 1}月${d.getDate()}日 星期${WEEK[d.getDay()]}`;
}

export function greeting(d = new Date()) {
  const h = d.getHours();
  if (h < 5) return "夜深了";
  if (h < 11) return "早上好";
  if (h < 13) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
}

export function relTime(iso: string) {
  const t = new Date(iso).getTime();
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  const d = new Date(iso);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export function dayLabel(day: string | null, today: string) {
  if (!day) return "未排期";
  if (day === today) return "今天";
  const a = new Date(day + "T00:00:00").getTime();
  const b = new Date(today + "T00:00:00").getTime();
  const diff = Math.round((a - b) / 86400000);
  if (diff === 1) return "明天";
  if (diff === -1) return "昨天";
  const d = new Date(day + "T00:00:00");
  return `${d.getMonth() + 1}月${d.getDate()}日 周${WEEK[d.getDay()]}`;
}

export const intervalLabel = (days: number) =>
  days < 30 ? `${days} 天` : days < 365 ? `${Math.round(days / 30)} 个月` : `${(days / 365).toFixed(1)} 年`;

export const sizeLabel = (b: number) =>
  b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(0)} KB` : `${(b / 1048576).toFixed(1)} MB`;

export const TASK_KINDS: Record<string, string> = { learn: "学习", review: "复习", practice: "练习", read: "阅读" };

export const REVISION_REASON: Record<string, string> = {
  before_ai_organize: "AI 整理前",
  before_ai_summary: "加入 AI 摘要前",
  before_restore: "恢复旧版本前",
  before_clear: "正文被清空前",
};

export const MATERIAL_STATUS: Record<string, { label: string; tone: string }> = {
  uploaded: { label: "排队中", tone: "muted" },
  extracting: { label: "解析中", tone: "muted" },
  ready: { label: "已就绪", tone: "ok" },
  partial: { label: "部分可用", tone: "warn" },
  failed: { label: "解析失败", tone: "bad" },
};

/* ---------- 日期（一律用本地日期字符串 YYYY-MM-DD，避免时区换算） ---------- */
export const ymd = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export const parseDay = (s: string) => {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
};
export const shiftDay = (s: string, days: number) => {
  const d = parseDay(s);
  d.setDate(d.getDate() + days);
  return ymd(d);
};
export const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];
/** 周一 = 0 */
export const weekdayIndex = (d: Date) => (d.getDay() + 6) % 7;
