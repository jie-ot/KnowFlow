import type { NextConfig } from "next";

// 编译成纯静态网页（out/），再由后端同源提供，运行时不需要 Node
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  devIndicators: false,
  agentRules: false,
};

export default nextConfig;
