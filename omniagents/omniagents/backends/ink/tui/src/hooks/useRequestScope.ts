import { useLayoutEffect, useRef, type DependencyList } from "react";

/** Latest request wins within a view; retiring the view invalidates every ticket. */
export class RequestScope {
  private generation = 0;
  private requests = new Map<string, number>();
  begin(key: string): () => boolean {
    const generation = this.generation;
    const request = (this.requests.get(key) ?? 0) + 1;
    this.requests.set(key, request);
    return () =>
      generation === this.generation && this.requests.get(key) === request;
  }
  invalidate(): void {
    this.generation++;
    this.requests.clear();
  }
}

export function useRequestScope(
  dependencies: DependencyList = [],
): RequestScope {
  const scope = useRef(new RequestScope()).current;
  useLayoutEffect(() => () => scope.invalidate(), [scope, ...dependencies]);
  return scope;
}
