// 把 next build 生成的 out/ 复制到 backend/app/web/，后端从那里提供网页
import { copyFileSync, mkdirSync, mkdtempSync, readdirSync, renameSync, rmSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "..", "out");
const backend = resolve(here, "..", "..", "backend", "app");
const target = join(backend, "web");
if (!existsSync(out)) throw new Error("没有找到 out/，请先运行 next build");
// Windows 上 Node 24 的 cpSync 递归复制可能直接终止进程；逐文件复制避免该问题。
function copyTree(source, destination, segmentPrefix = "") {
  mkdirSync(destination, { recursive: true });
  for (const entry of readdirSync(source, { withFileTypes: true })) {
    const src = join(source, entry.name);
    const name = segmentPrefix ? `${segmentPrefix}.${entry.name}` : entry.name;
    const dest = join(destination, name);
    // Next 16.3 在 Windows 导出时只替换 /，遗漏了路径中的反斜杠。
    // 客户端请求 __next.notes.__PAGE__.txt，需把对应片段目录还原为点分文件名。
    if (entry.isDirectory()) {
      if (segmentPrefix || entry.name.startsWith("__next.")) copyTree(src, destination, name);
      else copyTree(src, dest);
    }
    else if (entry.isFile()) copyFileSync(src, dest);
    else throw new Error(`不支持的构建文件：${src}`);
  }
}
const staging = mkdtempSync(join(backend, ".web-build-"));
const previous = staging + "-previous";
// 所有替换与清理均限制在 backend/app 内。
if ([target, staging, previous].some((path) => dirname(resolve(path)) !== backend)) throw new Error("无效的构建目标");
try {
  copyTree(out, staging);
  if (!existsSync(join(staging, "index.html"))) throw new Error("构建结果缺少首页");
  if (existsSync(target)) renameSync(target, previous);
  try {
    renameSync(staging, target);
  } catch (error) {
    if (existsSync(previous)) renameSync(previous, target);
    throw error;
  }
  if (existsSync(previous)) rmSync(previous, { recursive: true, force: true });
} finally {
  if (existsSync(staging)) rmSync(staging, { recursive: true, force: true });
}
console.log("已复制到 backend/app/web/");
