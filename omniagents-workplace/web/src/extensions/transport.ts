import type { StreamEvent } from "../api";
export interface AppTransport {
  path: string;
  factory: string;
  origin: string;
  request: (input: string, init?: RequestInit) => Promise<Response>;
  navigate: (path: string) => void;
  subscribe: (listener: (event: StreamEvent) => void) => () => void;
}
let current: AppTransport | undefined;
export const appTransport = () => current;
export const setAppTransport = (value: AppTransport) => { current = value; };
