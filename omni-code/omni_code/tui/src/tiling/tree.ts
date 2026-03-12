/**
 * Tiling window manager: types, tree utilities, and reducer.
 *
 * The layout is a binary tree where internal nodes are splits (horizontal/vertical)
 * and leaves are terminal tiles. This maps to nested Ink flexbox Boxes.
 */

// --- Types ---

export type SplitDirection = "horizontal" | "vertical";

export type TileType = "chat" | "terminal" | "supervisor" | "ssh" | "info" | "log" | "artifacts" | "diff" | "picker";

/** Tile types that are backed by a PTY session. */
export const PTY_TILE_TYPES: Set<TileType> = new Set(["chat", "terminal", "supervisor", "ssh"]);

export type TileLeaf = {
  type: "leaf";
  id: string;
  tileType: TileType;
  command: string[];
  cwd?: string;
  /** For ticket-scoped tiles, the ticket ID this tile belongs to. */
  ticketId?: string;
};

export type TileSplit = {
  type: "split";
  direction: SplitDirection;
  ratio: number; // 0.0–1.0, where the divider sits
  a: TileNode;
  b: TileNode;
};

export type TileNode = TileLeaf | TileSplit;

export type Rect = { x: number; y: number; w: number; h: number };

export type TilingState = {
  root: TileNode | null;
  focusedId: string | null;
  nextId: number;
};

export type TilingAction =
  | { type: "SPLIT"; tileType: TileType; command: string[]; cwd?: string; ticketId?: string }
  | { type: "REPLACE_TILE"; id: string; tileType: TileType; command: string[]; cwd?: string }
  | { type: "CLOSE_TILE"; id: string }
  | { type: "FOCUS_TILE"; id: string }
  | { type: "NAVIGATE"; direction: "up" | "down" | "left" | "right"; availCols: number; availRows: number }
  | { type: "RESIZE"; axis: "horizontal" | "vertical"; delta: number; availCols: number; availRows: number }
  | { type: "ROTATE" }
  | { type: "SWAP"; direction: "up" | "down" | "left" | "right"; availCols: number; availRows: number }
  | { type: "RESET" }
  | { type: "RESTORE"; state: TilingState };

// --- Tree utilities ---

export function getLeaves(node: TileNode): TileLeaf[] {
  if (node.type === "leaf") return [node];
  return [...getLeaves(node.a), ...getLeaves(node.b)];
}

export function containsLeaf(node: TileNode, id: string): boolean {
  if (node.type === "leaf") return node.id === id;
  return containsLeaf(node.a, id) || containsLeaf(node.b, id);
}

export function getFirstLeaf(node: TileNode): TileLeaf {
  if (node.type === "leaf") return node;
  return getFirstLeaf(node.a);
}

/** Deep clone a tree node. */
function cloneNode(node: TileNode): TileNode {
  if (node.type === "leaf") return { ...node };
  return { ...node, a: cloneNode(node.a), b: cloneNode(node.b) };
}

/** Compute bounding rectangles for all leaves. */
export function computeRects(node: TileNode, x: number, y: number, w: number, h: number): Map<string, Rect> {
  if (node.type === "leaf") {
    return new Map([[node.id, { x, y, w, h }]]);
  }
  const isH = node.direction === "horizontal";
  const aW = isH ? Math.floor(w * node.ratio) : w;
  const aH = isH ? h : Math.floor(h * node.ratio);
  const bX = isH ? x + aW : x;
  const bY = isH ? y : y + aH;
  const bW = isH ? w - aW : w;
  const bH = isH ? h : h - aH;
  const aRects = computeRects(node.a, x, y, aW, aH);
  const bRects = computeRects(node.b, bX, bY, bW, bH);
  return new Map([...aRects, ...bRects]);
}

/** Find the nearest neighbor tile in a direction using spatial proximity. */
export function findNeighbor(
  rects: Map<string, Rect>,
  fromId: string,
  direction: "up" | "down" | "left" | "right",
): string | null {
  const from = rects.get(fromId);
  if (!from) return null;
  const cx = from.x + from.w / 2;
  const cy = from.y + from.h / 2;
  let best: string | null = null;
  let bestDist = Infinity;
  for (const [id, r] of rects) {
    if (id === fromId) continue;
    const rx = r.x + r.w / 2;
    const ry = r.y + r.h / 2;
    if (direction === "left" && rx >= cx) continue;
    if (direction === "right" && rx <= cx) continue;
    if (direction === "up" && ry >= cy) continue;
    if (direction === "down" && ry <= cy) continue;
    const dist = Math.abs(rx - cx) + Math.abs(ry - cy);
    if (dist < bestDist) { bestDist = dist; best = id; }
  }
  return best;
}

/**
 * Get the direction of the parent split containing a leaf.
 * Returns null if the leaf is the root.
 */
function getParentDirection(root: TileNode, leafId: string): SplitDirection | null {
  if (root.type === "leaf") return null;
  if (
    (root.a.type === "leaf" && root.a.id === leafId) ||
    (root.b.type === "leaf" && root.b.id === leafId) ||
    containsLeaf(root.a, leafId) ||
    containsLeaf(root.b, leafId)
  ) {
    if (
      (root.a.type === "leaf" && root.a.id === leafId) ||
      (root.b.type === "leaf" && root.b.id === leafId)
    ) {
      return root.direction;
    }
    const inA = containsLeaf(root.a, leafId);
    return getParentDirection(inA ? root.a : root.b, leafId);
  }
  return null;
}

/**
 * Split a leaf node: replace it with a split containing the old leaf and a new leaf.
 * Direction alternates from the parent's direction (defaults to horizontal).
 */
function splitLeaf(root: TileNode, leafId: string, newLeaf: TileLeaf): TileNode {
  const parentDir = getParentDirection(root, leafId);
  const newDir: SplitDirection = parentDir === "horizontal" ? "vertical" : "horizontal";

  function visit(node: TileNode): TileNode {
    if (node.type === "leaf") {
      if (node.id === leafId) {
        return { type: "split", direction: newDir, ratio: 0.5, a: { ...node }, b: newLeaf };
      }
      return node;
    }
    return { ...node, a: visit(node.a), b: visit(node.b) };
  }
  return visit(root);
}

/**
 * Remove a leaf from the tree. The parent split is replaced by the surviving sibling.
 * Returns null if the removed leaf was the only node.
 */
function removeLeaf(root: TileNode, id: string): TileNode | null {
  if (root.type === "leaf") {
    return root.id === id ? null : root;
  }
  // If the leaf is a direct child, return the other child
  if (root.a.type === "leaf" && root.a.id === id) return root.b;
  if (root.b.type === "leaf" && root.b.id === id) return root.a;
  // Recurse into the side that contains the leaf
  if (containsLeaf(root.a, id)) {
    const newA = removeLeaf(root.a, id);
    return newA ? { ...root, a: newA } : root.b;
  }
  if (containsLeaf(root.b, id)) {
    const newB = removeLeaf(root.b, id);
    return newB ? { ...root, b: newB } : root.a;
  }
  return root;
}

/**
 * Find the nearest ancestor split matching an axis and adjust its ratio.
 * Returns a new tree root (immutable update).
 */
function resizeAxis(root: TileNode, leafId: string, axis: SplitDirection, delta: number): TileNode {
  // Walk from root, find the deepest split matching axis that contains the leaf
  function visit(node: TileNode): { node: TileNode; resized: boolean } {
    if (node.type === "leaf") return { node, resized: false };

    // Try to resize deeper first
    if (containsLeaf(node.a, leafId)) {
      const result = visit(node.a);
      if (result.resized) return { node: { ...node, a: result.node }, resized: true };
    } else if (containsLeaf(node.b, leafId)) {
      const result = visit(node.b);
      if (result.resized) return { node: { ...node, b: result.node }, resized: true };
    }

    // If this split matches the axis and contains the leaf, resize here
    if (node.direction === axis && containsLeaf(node, leafId)) {
      const isInA = containsLeaf(node.a, leafId);
      const step = 0.05 * delta;
      const adjustment = isInA ? step : -step;
      const newRatio = Math.max(0.15, Math.min(0.85, node.ratio + adjustment));
      return { node: { ...node, ratio: newRatio }, resized: true };
    }

    return { node, resized: false };
  }

  return visit(root).node;
}

/**
 * Toggle the direction of the parent split containing a leaf.
 * horizontal ↔ vertical. Returns a new tree root.
 */
function rotateParent(root: TileNode, leafId: string): TileNode {
  if (root.type === "leaf") return root;
  const isDirectChild =
    (root.a.type === "leaf" && root.a.id === leafId) ||
    (root.b.type === "leaf" && root.b.id === leafId);
  if (isDirectChild || (containsLeaf(root.a, leafId) && containsLeaf(root.b, leafId))) {
    // This is the parent split — toggle direction
    return {
      ...root,
      direction: root.direction === "horizontal" ? "vertical" : "horizontal",
    };
  }
  // Recurse into the side containing the leaf
  if (containsLeaf(root.a, leafId)) {
    return { ...root, a: rotateParent(root.a, leafId) };
  }
  if (containsLeaf(root.b, leafId)) {
    return { ...root, b: rotateParent(root.b, leafId) };
  }
  return root;
}

/**
 * Swap two leaves in the tree by exchanging their positions.
 * Returns a new tree root.
 */
function swapLeaves(root: TileNode, idA: string, idB: string): TileNode {
  // First pass: find the leaf data for both
  const leaves = getLeaves(root);
  const leafA = leaves.find(l => l.id === idA);
  const leafB = leaves.find(l => l.id === idB);
  if (!leafA || !leafB) return root;

  // Second pass: replace each leaf's position with the other's data
  function visit(node: TileNode): TileNode {
    if (node.type === "leaf") {
      if (node.id === idA) return { ...leafB! };
      if (node.id === idB) return { ...leafA! };
      return node;
    }
    return { ...node, a: visit(node.a), b: visit(node.b) };
  }
  return visit(root);
}

/** Replace a leaf in-place with new leaf data, preserving its position in the tree. */
function replaceLeaf(root: TileNode, id: string, newLeaf: TileLeaf): TileNode {
  if (root.type === "leaf") {
    return root.id === id ? newLeaf : root;
  }
  return { ...root, a: replaceLeaf(root.a, id, newLeaf), b: replaceLeaf(root.b, id, newLeaf) };
}

// --- Reducer ---

export const INITIAL_STATE: TilingState = {
  root: null,
  focusedId: null,
  nextId: 0,
};

export function tilingReducer(state: TilingState, action: TilingAction): TilingState {
  switch (action.type) {
    case "SPLIT": {
      const id = String(state.nextId + 1);
      const newLeaf: TileLeaf = {
        type: "leaf",
        id,
        tileType: action.tileType,
        command: action.command,
        cwd: action.cwd,
        ticketId: action.ticketId,
      };

      let newRoot: TileNode;
      if (!state.root) {
        // First tile
        newRoot = newLeaf;
      } else if (!state.focusedId) {
        // No focus — split root
        newRoot = {
          type: "split",
          direction: "horizontal",
          ratio: 0.5,
          a: state.root,
          b: newLeaf,
        };
      } else {
        // Split the focused tile
        newRoot = splitLeaf(state.root, state.focusedId, newLeaf);
      }

      return {
        ...state,
        root: newRoot,
        focusedId: id,
        nextId: state.nextId + 1,
      };
    }

    case "REPLACE_TILE": {
      if (!state.root) return state;
      const newLeaf: TileLeaf = {
        type: "leaf",
        id: action.id,
        tileType: action.tileType,
        command: action.command,
        cwd: action.cwd,
      };
      return {
        ...state,
        root: replaceLeaf(state.root, action.id, newLeaf),
        focusedId: action.id,
      };
    }

    case "CLOSE_TILE": {
      if (!state.root) return state;
      const newRoot = removeLeaf(state.root, action.id);
      let newFocused = state.focusedId;
      if (state.focusedId === action.id) {
        if (newRoot) {
          const leaves = getLeaves(newRoot);
          newFocused = leaves[0]?.id ?? null;
        } else {
          newFocused = null;
        }
      }
      return {
        ...state,
        root: newRoot,
        focusedId: newFocused,
      };
    }

    case "FOCUS_TILE": {
      return { ...state, focusedId: action.id };
    }

    case "NAVIGATE": {
      if (!state.root || !state.focusedId) return state;
      const rects = computeRects(state.root, 0, 0, action.availCols, action.availRows);
      const neighbor = findNeighbor(rects, state.focusedId, action.direction);
      if (neighbor) {
        return { ...state, focusedId: neighbor };
      }
      return state;
    }

    case "RESIZE": {
      if (!state.root || !state.focusedId) return state;
      const newRoot = resizeAxis(state.root, state.focusedId, action.axis, action.delta);
      return { ...state, root: newRoot };
    }

    case "SWAP": {
      if (!state.root || !state.focusedId) return state;
      const rects = computeRects(state.root, 0, 0, action.availCols, action.availRows);
      const neighbor = findNeighbor(rects, state.focusedId, action.direction);
      if (!neighbor) return state;
      const newRoot = swapLeaves(state.root, state.focusedId, neighbor);
      return { ...state, root: newRoot, focusedId: neighbor };
    }

    case "ROTATE": {
      if (!state.root || !state.focusedId || state.root.type === "leaf") return state;
      const newRoot = rotateParent(state.root, state.focusedId);
      return { ...state, root: newRoot };
    }

    case "RESET": {
      return INITIAL_STATE;
    }

    case "RESTORE": {
      return action.state;
    }

    default:
      return state;
  }
}
