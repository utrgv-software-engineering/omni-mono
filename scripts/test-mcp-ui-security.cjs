// Exercise the actual bundled schemas, not a parallel reimplementation.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const { transformSync } = require('esbuild');
const target = require.resolve('@mcp-ui/client').replace(/index\.js$/, 'index.mjs');
for (const format of ['esm', 'cjs']) {
  const source = fs.readFileSync(format === 'esm' ? target : target.replace(/index\.mjs$/, 'index.js'), 'utf8');
  const code = format === 'esm'
    ? transformSync(source + '\nexport { l as __securityTestZod };', { format: 'cjs', loader: 'js' }).code
    : source.replace('T.AppBridge=', 'T.__securityTestZod=l,T.AppBridge=');
  const mod = { exports: {} };
  vm.runInNewContext(code, { module: mod, exports: mod.exports, require: createRequire(target),
    console, Map, Set, Array, URL, TextEncoder, TextDecoder, AbortController, setTimeout, clearTimeout });
  const z = mod.exports.__securityTestZod;
let visited = 0;
const element = z.string().refine(() => { visited++; return true; });
for (const [schema, input] of [
  [z.array(element).max(2), Array(10000).fill('value')],
  [z.array(element).length(2), Array(10000).fill('value')],
  [z.set(element).max(2), new Set(['a','b','c'])],
  [z.map(element, element).check(z.maxSize(2)), new Map([['a','b'],['c','d'],['e','f']])],
]) {
  const result = schema.safeParse(input);
  assert.equal(result.success, false);
  assert.equal(result.error.issues.length, 1);
  assert.equal(result.error.issues[0].code, 'too_big');
}
assert.equal(visited, 0, 'oversized collections must fail before parsing elements');
assert.deepEqual(Array.from(z.array(z.string()).max(2).parse(['ok'])), ['ok']);
assert.deepEqual(Array.from(z.array(z.string()).overwrite(a => a.slice(0, 2)).max(2).parse(['a','b','c'])), ['a','b']);
}
console.log('Bundled MCP UI collection-size security regressions passed');
