// Backport Zod's early collection-size checks to the copy bundled in MCP UI 7.1.1.
// Upstream: colinhacks/zod@a0ebf97 (MIT). Remove when MCP UI bundles Zod >=4.6.6.
// Pin and fail closed on changed upstream output; a lockfile override alone misses this copy.
const fs = require('node:fs');
const path = require('node:path');
let entry;
try { entry = require.resolve('@mcp-ui/client'); }
catch (error) {
  if (error.code === 'MODULE_NOT_FOUND') process.exit(0); // A workspace install without MCP UI.
  throw error;
}
const root = path.dirname(entry);
const metadata = JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf8'));
if (metadata.version !== '7.1.1') throw new Error('Review the MCP UI security backport for this version');
const marker = '/* omni collection size gate */';
const helper = `${marker}
function omniCollectionSizeGate(inst) {
  (inst._zod.deferred ??= []).push(() => {
    const run = inst._zod.run;
    inst._zod.run = (payload, ctx) => {
      const value = payload.value;
      const size = inst._zod.def.type === 'array' && Array.isArray(value) ? value.length
        : inst._zod.def.type === 'map' && value instanceof Map ? value.size
        : inst._zod.def.type === 'set' && value instanceof Set ? value.size : -1;
      if (!ctx?.skipChecks && ctx?.direction !== 'backward') {
        for (const check of inst._zod.def.checks ?? []) {
          const def = check._zod.def;
          if (!/_(length|size)$|_equals$/.test(def.check)) break;
          const max = def.maximum ?? def.length ?? def.size;
          if (max >= 0 && size > max && (!def.when || def.when(payload))) {
            const start = payload.issues.length;
            check._zod.check(payload);
            if (payload.issues.length > start) {
              payload.issues[start].continue = false;
              payload.issues[start].schema = inst;
              return payload;
            }
          }
        }
      }
      return run(payload, ctx);
    };
  });
}
`;
for (const filename of ['index.mjs', 'index.js']) {
  const target = path.join(root, filename);
  let source = fs.readFileSync(target, 'utf8');
  if (source.includes(marker)) continue;
  for (const type of ['Array', 'Map', 'Set']) {
    const pattern = new RegExp('(\\$Zod' + type + '"\\s*,\\s*\\(e,\\s*i\\)\\s*=>\\s*\\{\\s*k\\.init\\(e,\\s*i\\),)');
    if (!pattern.test(source)) throw new Error('Cannot locate bundled Zod ' + type);
    source = source.replace(pattern, '$1omniCollectionSizeGate(e),');
  }
  fs.writeFileSync(target, helper + source);
}
