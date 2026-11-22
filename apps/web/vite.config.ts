import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { readDevelopmentApiOrigin } from "./e2eRuntimeConfig.js";
import { createLoopbackPeerGuard } from "./viteLoopbackGuard.js";

function loopbackPeerGuard(): Plugin {
  return {
    name: "ai-canvas-loopback-peer-guard",
    enforce: "pre",
    configureServer(server) {
      server.middlewares.use(createLoopbackPeerGuard());
    },
  };
}

function documentFramingProtection(): Plugin {
  return {
    name: "ai-canvas-document-framing-protection",
    enforce: "pre",
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const pathname = (request.url ?? "/").split(/[?#]/, 1)[0] ?? "/";
        if (pathname !== "/api" && !pathname.startsWith("/api/")) {
          response.setHeader("X-Frame-Options", "DENY");
          response.setHeader(
            "Content-Security-Policy",
            "frame-ancestors 'none'",
          );
        }
        next();
      });
    },
  };
}

function instanceMarker(): Plugin {
  return {
    name: "ai-canvas-development-instance",
    configureServer(server) {
      server.middlewares.use("/__ai_canvas_instance", (_request, response) => {
        response.statusCode = 200;
        response.setHeader("Cache-Control", "no-store");
        response.setHeader("Content-Type", "text/plain; charset=utf-8");
        response.end(process.env.AI_CANVAS_INSTANCE_ID ?? "unconfigured");
      });
    },
  };
}

export default defineConfig({
  plugins: [
    loopbackPeerGuard(),
    documentFramingProtection(),
    react(),
    instanceMarker(),
  ],
  server: {
    proxy: {
      "/api": {
        target: readDevelopmentApiOrigin(),
        changeOrigin: false,
      },
    },
  },
});
