import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Build to static files in out/, which the desktop app serves itself.
  output: "export",
};

export default nextConfig;
