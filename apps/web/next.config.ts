import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  transpilePackages: ["@code-genome/contracts"],
};

export default nextConfig;

