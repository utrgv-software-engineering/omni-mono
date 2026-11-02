// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { PublishedChat } from "./PublishedChat";
const { session, client } = vi.hoisted(() => ({
  session: { load: vi.fn(async () => "factory-personal"), on: vi.fn(() => () => {}), send: vi.fn(async () => ({})) },
  client: { connect: vi.fn(async () => {}) },
}));
vi.mock("@omniagents/chat-ui/rpc-context", () => ({ useRPCClient: () => client, useRPCConnected: () => true }));
vi.mock("@omniagents/chat-ui/hooks/use-chat-session", () => ({ useChatSession: () => ({ controller: session, items: [{content:"INTERNAL FINAL MUST NOT APPEAR"}], pendingApprovals: new Map(), phase:"idle", thinking:false }), useSessionField: () => [null] }));
vi.mock("@omniagents/chat-ui/hooks/use-realtime-voice", () => ({ useRealtimeVoice: () => ({actor:{}, open:vi.fn()}) }));
vi.mock("@xstate/react", () => ({ useSelector: () => false }));
vi.mock("@omniagents/chat-ui/components/MessageList", () => ({ MessageList: ({items}:any) => <div>{items.map((m:any)=><p key={m.message_id}>{m.content}</p>)}</div> }));
vi.mock("@omniagents/chat-ui/components/Input", () => ({ Input: () => <div>Composer</div> }));
vi.mock("@omniagents/chat-ui/components/omni-avatar/OmniAvatar", () => ({ OmniAvatar: ({onClick}:any) => <button onClick={onClick}>Omni</button> }));
vi.mock("@omniagents/chat-ui/components/ModelSessionControls", () => ({ ModelSessionControls: () => null }));
vi.mock("@omniagents/chat-ui/components/EscalationBanner", () => ({ EscalationBanner: () => null }));
Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT:true});
afterEach(() => vi.unstubAllGlobals());
it("renders only the independent public feed, preserves literal prefixes, and opens activity", async () => {
  const fetcher=vi.fn(async (_url: string) => ({ok:true,json:async()=>({messages:[{seq:1,id:"one",role:"assistant",author:"agent",text:"[worker message] intentionally published",created_at:1}],cursor:1,has_more:false})}));
  vi.stubGlobal("fetch",fetcher);
  const node=document.createElement("div"); document.body.append(node); const root=createRoot(node); const present=vi.fn();
  try {
    await act(async()=>root.render(<PublishedChat sessionId="factory-personal" active onPresent={present}/>));
    expect(node.textContent).toContain("[worker message] intentionally published");
    expect(node.textContent).not.toContain("INTERNAL FINAL");
    expect(fetcher.mock.calls[0][0]).toContain("/feed?after=0");
    await act(async()=>node.querySelector("button")!.click());
    expect(present).toHaveBeenCalledWith(expect.objectContaining({kind:"activity",id:"factory-personal"}));
    expect(session.send).not.toHaveBeenCalled();
    await act(async()=>root.render(<PublishedChat sessionId="factory-personal" active showAvatar={false} onPresent={present}/>));
    expect(node.textContent).not.toContain("Omni");
    await act(async()=>root.render(<PublishedChat sessionId="factory-personal" active showAvatar onPresent={present}/>));
    expect(node.textContent).toContain("Omni");
  } finally { await act(async()=>root.unmount()); node.remove(); }
});
