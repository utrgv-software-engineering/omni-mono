/** `/factory/runs/<id>`: the run page's id (kept apart so the page loads lazily). */
export function parseFactoryRunPath(path: string): number | null {
  const match = /^\/factory\/runs\/(\d+)\/?$/.exec(path);
  return match ? Number(match[1]) : null;
}
