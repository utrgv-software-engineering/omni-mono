import fs from 'node:fs';
import { createRequire } from 'node:module';
import process from 'node:process';

const require = createRequire(
  new URL('../../omniagents/backends/web/ui/package.json', import.meta.url),
);
const Ajv2020 = require('ajv/dist/2020').default;
const { openrpcDocument } = require('@open-rpc/meta-schema');


const documentPath = process.argv[2];
if (!documentPath) {
  throw new Error('OpenRPC document path is required');
}

const document = JSON.parse(fs.readFileSync(documentPath, 'utf8'));
const jsonSchemaTools = JSON.parse(
  fs.readFileSync(
    new URL('../../protocol/openrpc/meta/json-schema-tools.json', import.meta.url),
    'utf8',
  ),
);
const ajv = new Ajv2020({
  allErrors: true,
  strict: false,
  validateSchema: false,
});
ajv.addSchema(jsonSchemaTools);
ajv.addSchema(jsonSchemaTools, 'https://meta.json-schema.tools');
const validate = ajv.compile(openrpcDocument);
if (!validate(document)) {
  console.error(JSON.stringify(validate.errors, null, 2));
  process.exit(1);
}
