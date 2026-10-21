import 'dotenv/config';

import { BlockList, isIPv4, isIPv6 } from 'node:net';

import fastifyStatic from '@fastify/static';
import fastifyWebsocket from '@fastify/websocket';
import Fastify from 'fastify';
import { existsSync, readFileSync } from 'fs';
import { join, resolve } from 'path';

// Server mode always runs as "development" so util.ts resolves paths from project root
process.env.NODE_ENV = process.env.NODE_ENV || 'development';

import { uuidv4 } from '@/lib/uuid';
import { registerChatWsRoute } from '@/server/chat-ws';
import { wireClientManagers, wireGlobalHandlers } from '@/server/managers';
import { redactProxyUrlForLog, setupProxyRewriter } from '@/server/proxy-rewriter';
import { ServerStore } from '@/server/store';
import { registerVoiceRoutes, VOICE_HTTP_PREFIX } from '@/server/voice-http';
import { WsHandler } from '@/server/ws-handler';
import { resolveWsTokenSecret, signWsToken, verifyWsToken } from '@/server/ws-token';

// Process-level crash visibility. Log only — do not exit. The server
// process is typically managed externally (systemd, pm2, docker), so
// killing on an unhandled rejection would mask the underlying bug under
// a restart loop. Log loudly and let the operator decide.
process.on('unhandledRejection', (reason) => {
  console.error('[fatal] unhandledRejection:', reason);
});
process.on('uncaughtException', (err) => {
  console.error('[fatal] uncaughtException:', err);
});

const PORT = parseInt(process.env['PORT'] ?? '3001', 10);
const HOST = process.env['HOST'] ?? '0.0.0.0';

/**
 * Build the allowlist for /api/ws-token. Loopback is always trusted; additional
 * networks can be opted in via OMNI_TRUSTED_CIDRS (comma-separated).
 *
 * Example for Tailscale: OMNI_TRUSTED_CIDRS=100.64.0.0/10,fd7a:115c:a1e0::/48
 *
 * Anything outside this allowlist must obtain a signed token from
 * /api/ws-token (which gates /ws auth) and pass it as ?token=.
 */
function buildTokenAllowList(): { check: (addr: string) => boolean; describe: () => string } {
  const list = new BlockList();
  list.addAddress('127.0.0.1', 'ipv4');
  list.addAddress('::1', 'ipv6');

  const cidrs = (process.env['OMNI_TRUSTED_CIDRS'] ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

  for (const cidr of cidrs) {
    const slash = cidr.indexOf('/');
    if (slash < 0) {
      console.warn(`[auth] OMNI_TRUSTED_CIDRS entry missing prefix length, skipping: "${cidr}"`);
      continue;
    }
    const base = cidr.slice(0, slash);
    const prefix = parseInt(cidr.slice(slash + 1), 10);
    const family = isIPv6(base) ? 'ipv6' : isIPv4(base) ? 'ipv4' : null;
    if (!family || !Number.isFinite(prefix)) {
      console.warn(`[auth] OMNI_TRUSTED_CIDRS invalid entry, skipping: "${cidr}"`);
      continue;
    }
    list.addSubnet(base, prefix, family);
  }

  const check = (addr: string): boolean => {
    if (!addr) {
      return false;
    }
    // Strip IPv4-mapped IPv6 prefix so 100.x addresses match the IPv4 rules
    const normalized = addr.startsWith('::ffff:') ? addr.slice(7) : addr;
    if (isIPv4(normalized)) {
      return list.check(normalized, 'ipv4');
    }
    if (isIPv6(normalized)) {
      return list.check(normalized, 'ipv6');
    }
    return false;
  };

  return { check, describe: () => (cidrs.length > 0 ? `loopback + ${cidrs.join(', ')}` : 'loopback only') };
}

const main = async () => {
  const fastify = Fastify({
    logger: {
      serializers: {
        req: (request) => ({ method: request.method, url: redactProxyUrlForLog(request.url ?? '') }),
      },
    },
  });

  // Tokens minted at /api/ws-token are signed with this secret and verified
  // at /ws and /ws/chat.
  const wsTokenSecret = resolveWsTokenSecret();

  const tokenAllowList = buildTokenAllowList();
  console.log(`[auth] /api/ws-token trusted networks: ${tokenAllowList.describe()}`);

  // WebSocket plugin
  await fastify.register(fastifyWebsocket);

  // Readiness + version probe for locally-spawned daemons (WSL backend mode).
  // The Windows shell polls this after spawning the daemon and before creating
  // the BrowserWindow. OMNI_LAUNCHER_VERSION is set by the spawner because the
  // payload layout has no package.json at ../../ (unlike a source checkout).
  fastify.get('/api/health', (_req, reply) => {
    let version = process.env['OMNI_LAUNCHER_VERSION'] ?? '';
    if (!version) {
      try {
        const pkg = JSON.parse(readFileSync(join(__dirname, '../../package.json'), 'utf-8'));
        version = (pkg as { version?: string }).version ?? '0.0.0';
      } catch {
        version = '0.0.0';
      }
    }
    reply.send({ ok: true, version });
  });

  // Token endpoint — restricted to trusted networks (loopback by default;
  // extend via OMNI_TRUSTED_CIDRS, e.g. "100.64.0.0/10" for Tailscale).
  // The returned token is signed (HMAC); /ws verifies the signature.
  fastify.get('/api/ws-token', (request, reply) => {
    const addr = request.socket.remoteAddress ?? '';
    if (!tokenAllowList.check(addr)) {
      reply.code(403).send({ error: 'Forbidden' });
      return;
    }
    // Short TTL (5 min) — the renderer fetches a fresh one on each connect/
    // reconnect, so we don't need long-lived tokens floating around.
    reply.send({ token: signWsToken(wsTokenSecret, { sessionId: uuidv4() }, 5 * 60) });
  });

  // WebSocket handler
  const wsHandler = new WsHandler();
  const store = new ServerStore();

  // Wire global (shared) IPC handlers — store, util, config, project, code, chat, sandbox, install
  const { cleanupGlobalManagers, processManager, residentAgentManager } = await wireGlobalHandlers({
    wsHandler,
    store,
  });
  // Reverse proxy URL rewriting for internal services (chat, sandbox, etc.).
  // Share the ws-token allowlist so /proxy/_register honors OMNI_TRUSTED_CIDRS too.
  setupProxyRewriter(fastify, wsHandler, tokenAllowList.check);

  // Local voice: STT/TTS run on this host.
  registerVoiceRoutes(fastify);
  console.log(`[voice] local voice routes registered at ${VOICE_HTTP_PREFIX}`);

  // WebSocket route — each new connection gets its own manager instances.
  // Clients send a sessionId query param; if the server has an existing session
  // for that ID the client reattaches to its running managers/containers.
  await fastify.register(async function wsRoutes(f) {
    f.get('/ws', { websocket: true }, (socket, request) => {
      const url = new URL(request.url, `http://${request.hostname}`);
      const token = url.searchParams.get('token');
      // Auth is the signed token from /api/ws-token (the browser WebSocket
      // API can't send Bearer headers on the upgrade).
      if (!token || !verifyWsToken(wsTokenSecret, token)) {
        socket.close(4401, 'Unauthorized');
        return;
      }
      const sessionId = url.searchParams.get('sessionId') ?? undefined;
      wsHandler.addClient(
        socket,
        (session) => {
          const cleanup = wireClientManagers({
            handle: session.handle,
            sendToWindow: session.sendToWindow,
            store,
            processManager,
          });
          session.setCleanup(cleanup);
        },
        sessionId
      );
    });

    // chat-v1's public binding (docs/chat-v1-plan.md): JSON-RPC over WS for
    // external clients (mobile apps, network bridges). Same token auth as
    // /ws, plus raw bridge keys from OMNI_CHAT_BRIDGE_KEYS.
    registerChatWsRoute(f, {
      wsTokenSecret,
      residentManager: residentAgentManager,
      bridgeKeys: (process.env['OMNI_CHAT_BRIDGE_KEYS'] ?? '')
        .split(',')
        .map((k) => k.trim())
        .filter((k) => k.length > 0),
    });
  });

  // MCP-Apps sandbox proxy. Mirrors the ``mcp-sandbox://`` Electron
  // protocol (see src/main/index.ts) but with a same-origin caveat:
  // in browser mode the proxy URL shares an origin with the renderer,
  // so guest UIs get less isolation than under Electron. The mcp-ui
  // double-iframe + document.write pattern still buys some separation
  // for the rawhtml flow. Use Electron for full cross-origin sandboxing.
  const sandboxHtmlPath = resolve(__dirname, '../..', 'assets', 'mcp-sandbox', 'index.html');
  fastify.get('/mcp-sandbox/index.html', (_request, reply) => {
    if (!existsSync(sandboxHtmlPath)) {
      return reply.code(404).send('Not found');
    }
    return reply
      .header('Content-Type', 'text/html; charset=utf-8')
      .header(
        'Content-Security-Policy',
        [
          "default-src 'none'",
          "script-src 'unsafe-inline' https:",
          "style-src 'unsafe-inline' https:",
          'font-src https: data:',
          'img-src https: data: blob:',
          'connect-src https: wss: ws: data: blob:',
          'frame-src about: data: blob: https: http:',
        ].join('; ')
      )
      .send(readFileSync(sandboxHtmlPath));
  });

  // Serve the built browser renderer as static files (populated by
  // `build:server`). In dev:server mode this static dir is intentionally NOT
  // rebuilt on save — do dev against the Vite dev server on `http://<host>:5173`,
  // which has HMR and proxies /api, /ws, and /proxy back here. The `:3001`
  // static path then just serves whatever build:server last produced (or warns
  // if absent), so it's only useful as a production-like smoke check.
  const staticDir = resolve(__dirname, '../browser');
  if (existsSync(staticDir)) {
    await fastify.register(fastifyStatic, {
      root: staticDir,
      prefix: '/',
    });
  } else {
    fastify.log.warn(`Static dir not found: ${staticDir}. Renderer will not be served.`);
  }

  // SPA fallback: serve index.html for non-API, non-WS routes
  fastify.setNotFoundHandler((_request, reply) => {
    const indexPath = join(staticDir, 'index.html');
    if (existsSync(indexPath)) {
      return reply.sendFile('index.html');
    }
    return reply.code(404).send({ error: 'Not found' });
  });

  // Graceful shutdown — clean up all persistent sessions + global managers.
  // Guarded: a second signal while cleanup is in flight forces an immediate
  // exit (the serve children were signalled too and finish their own
  // teardown), and a hard timeout keeps a hung teardown from lingering
  // forever as an orphaned dev-server process.
  let shuttingDown = false;
  const shutdown = async () => {
    if (shuttingDown) {
      fastify.log.warn('Second signal during shutdown — forcing exit');
      process.exit(1);
    }
    shuttingDown = true;
    fastify.log.info('Shutting down...');
    const forceExitTimer = setTimeout(() => {
      fastify.log.error('Shutdown timed out after 30s, forcing exit');
      process.exit(1);
    }, 30_000);
    forceExitTimer.unref();
    await wsHandler.cleanupAllSessions();
    await cleanupGlobalManagers();
    await fastify.close();
    process.exit(0);
  };

  process.on('SIGTERM', () => void shutdown());
  process.on('SIGINT', () => void shutdown());

  // Start
  await fastify.listen({ port: PORT, host: HOST });
  fastify.log.info(`Server listening on http://${HOST}:${PORT}`);
};

main().catch((err) => {
  console.error('Failed to start server:', err);
  process.exit(1);
});
