// Shim for dynamic require in ESM context
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
export { require };
