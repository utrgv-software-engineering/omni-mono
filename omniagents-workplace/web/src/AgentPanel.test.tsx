// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { AgentPanel } from "./AgentPanel";
const planets = vi.hoisted(() => [] as Record<string, unknown>[]);
vi.mock("@omniagents/chat-ui/components/omni-avatar/planet", async original => ({...(await original<object>()), OmniPlanet: class {constructor(_: unknown, options: Record<string, unknown>) {planets.push(options);} setPaused() {} dispose() {}}}));
vi.mock("./Memory", () => ({MemoryApp: ({agent}: {agent: boolean}) => <div>{agent ? "Scoped memory" : "All memory"}</div>}));
vi.mock("@omniagents/chat-ui/components/ElicitationCard", () => ({ElicitationCard: () => null}));
Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true});
it("loads structured activity, worker activity, and scoped memory independently", async () => {
  const fetcher = vi.fn(async (url: string) => ({ok:true,json:async()=> url.endsWith("/agents") ? {agents:[{worker_id:"w1",name:"Researcher",task:"Read sources",status:"completed"}]} : {items:[{item_id:"t1",seq:1,kind:"tool_call",status:"completed",content:{tool:"read_file",input:{path:"/workspace/report"},output:"File contents"}}], attention:[],next_cursor:null}}));
  vi.stubGlobal("fetch",fetcher);
  const node = document.createElement("div");document.body.append(node);const root=createRoot(node);
  try {
    await act(async()=>root.render(<AgentPanel chatId="chat-one" tab="activity" onTab={()=>{}}/>));
    expect(node.textContent).toContain("read_file");
    expect(node.textContent).toContain("File contents");
    expect(fetcher.mock.calls.some(([url])=>url.includes("/chat-one/activity"))).toBe(true);
    await act(async()=>root.render(<AgentPanel chatId="chat-one" tab="agents" onTab={()=>{}}/>));
    expect(node.textContent).toContain("Researcher");
    expect(node.textContent).toContain("Read sources");
    await act(async()=>[...node.querySelectorAll("button")].find(b=>b.getAttribute("aria-label")==="View activity: Researcher")!.click());
    expect(fetcher.mock.calls.some(([url])=>url.includes("worker=w1"))).toBe(true);
    await act(async()=>root.render(<AgentPanel chatId="chat-one" tab="memory" onTab={()=>{}}/>));
    expect(node.textContent).toContain("Scoped memory");
    expect(node.textContent).not.toContain("All memory");
    expect(fetcher.mock.calls.every(([url])=> !url.includes("runtime"))).toBe(true);
  } finally { await act(async()=>root.unmount());node.remove();vi.unstubAllGlobals(); }
});

it("draws the agent in its own colours, as the chat list does", async () => {
  const { agentPalette } = await import("@omniagents/chat-ui/components/omni-avatar/agent-palette");
  vi.stubGlobal("fetch", vi.fn(async () => ({ok:true,json:async()=>({agents:[],items:[],attention:[],next_cursor:null})})));
  const node = document.createElement("div");document.body.append(node);const root=createRoot(node);
  try {
    planets.length = 0;
    await act(async()=>root.render(<AgentPanel agentId="generalist" chatId="chat-one" tab="activity" onTab={()=>{}}/>));
    expect(planets.at(-1)!.palette).toEqual(agentPalette("generalist"));
    planets.length = 0;
    await act(async()=>root.render(<AgentPanel key="no-id" chatId="chat-one" tab="activity" onTab={()=>{}}/>));
    // Without an ID it keeps the planet's own colours rather than none.
    expect(planets.at(-1)).not.toHaveProperty("palette");
  } finally { act(()=>root.unmount()); node.remove(); vi.unstubAllGlobals(); }
});
