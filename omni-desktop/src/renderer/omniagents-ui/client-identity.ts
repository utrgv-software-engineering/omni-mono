import type { Identity } from '@omniagents/client/protocol/gui-v2';

import { version } from '../../../package.json';

/** How the desktop introduces itself to the agent runtimes it connects to. */
export const CLIENT_IDENTITY: Identity = { name: 'omni-desktop', version };
