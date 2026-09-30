import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {},
  experimental: {
    // Turbopack's minifier rewrites a WebAssembly string inside Cesium as a
    // template literal containing octal escapes, which browsers refuse to
    // parse ("Octal escape sequences are not allowed in template strings").
    // That module then never runs and the map stays blank in a production
    // build. `npm run check:bundle` fails if any built chunk does not parse,
    // so this can be revisited by turning the flag back on and running it.
    turbopackMinify: false,
  },
};

export default nextConfig;
