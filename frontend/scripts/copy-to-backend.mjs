// 把 next build 生成的 out/ 复制到 backend/app/web/，后端从那里提供网页
import { cpSync, rmSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "..", "out");
const target = join(here, "..", "..", "backend", "app", "web");
if (!existsSync(out)) throw new Error("没有找到 out/，请先运行 next build");
rmSync(target, { recursive: true, force: true });
cpSync(out, target, { recursive: true });
console.log("已复制到 backend/app/web/");
