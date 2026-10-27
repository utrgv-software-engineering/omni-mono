#!/usr/bin/env node
/* eslint-disable  @typescript-eslint/no-require-imports */

const fs = require('fs');
const crypto = require('crypto');
const path = require('path');

// The launcher is a workspace of the monorepo, locked by the root lockfile.
// Its own version is dropped so a version bump alone keeps the cache.
const lockfilePath = path.join(process.cwd(), '..', 'package-lock.json');
const lockfile = JSON.parse(fs.readFileSync(lockfilePath, 'utf8'));
delete lockfile.packages?.['omni-desktop']?.version;

const hash = crypto.createHash('sha256').update(JSON.stringify(lockfile)).digest('hex');

// Output just the hash for use in GitHub Actions
console.log(hash);
