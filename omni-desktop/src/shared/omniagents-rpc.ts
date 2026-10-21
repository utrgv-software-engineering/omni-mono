import type { JsonRpcError } from '@omniagents/protocol/gui-v2';

export class OmniagentsRpcError extends Error {
  readonly code: number;
  readonly data?: unknown;

  constructor(error: JsonRpcError) {
    super(error.message);
    this.name = 'OmniagentsRpcError';
    this.code = error.code;
    this.data = error.data;
  }
}
