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
    expect(fetcher.mock.calls[0][0]).toContain("/feed?latest=true");
    await act(async()=>node.querySelector("button")!.click());
    expect(present).toHaveBeenCalledWith(expect.objectContaining({kind:"activity",id:"factory-personal"}));
    expect(session.send).not.toHaveBeenCalled();
    await act(async()=>root.render(<PublishedChat sessionId="factory-personal" active showAvatar={false} onPresent={present}/>));
    expect(node.textContent).not.toContain("Omni");
    await act(async()=>root.render(<PublishedChat sessionId="factory-personal" active showAvatar onPresent={present}/>));
    expect(node.textContent).toContain("Omni");
  } finally { await act(async()=>root.unmount()); node.remove(); }
});

it("gives each person's message its sender and leaves the agent's unattributed", async () => {
  const { publicMessageItems } = await import("./PublishedChat");
  const items = publicMessageItems([
    { seq: 1, id: "a", role: "user", author: "priya", author_name: "Priya Shah", text: "hi", created_at: 1 },
    { seq: 2, id: "b", role: "assistant", author: "app", text: "hello", created_at: 2 },
  ]);
  expect(items[0].sender).toEqual({ name: "Priya Shah" });
  expect(items[1].sender).toBeUndefined();
});

it("opens on the newest messages and reads earlier ones before them", async () => {
  const message = (seq: number) => ({seq, id: `m${seq}`, role: "assistant", author: "agent", text: `message ${seq}`, created_at: seq});
  const urls: string[] = [];
  const fetcher = vi.fn(async (url: string) => {
    urls.push(url);
    const body = url.includes("latest=true")
      ? {messages: [message(3), message(4)], cursor: 4, earlier: 3}
      : url.includes("before=3")
        ? {messages: [message(1), message(2)], cursor: 2, earlier: null}
        : {messages: [], cursor: 4, has_more: false};
    return {ok: true, json: async () => body};
  });
  vi.stubGlobal("fetch", fetcher);
  let loadEarlier: (() => Promise<void>) | undefined;
  let earlierHistory = "";
  const list = await import("@omniagents/chat-ui/components/MessageList");
  vi.spyOn(list, "MessageList").mockImplementation(((props: any) => {
    loadEarlier = props.onLoadEarlier;
    earlierHistory = props.earlierHistory;
    return <div>{props.items.map((m: any) => <p key={m.message_id}>{m.content}</p>)}</div>;
  }) as any);
  const node = document.createElement("div"); document.body.append(node); const root = createRoot(node);
  try {
    await act(async () => root.render(<PublishedChat sessionId="factory-personal" active onPresent={vi.fn()} />));
    expect([...node.querySelectorAll("p")].map(p => p.textContent)).toEqual(["message 3", "message 4"]);
    expect(earlierHistory).toBe("more");
    await act(async () => { await loadEarlier!(); });
    expect([...node.querySelectorAll("p")].map(p => p.textContent)).toEqual(["message 1", "message 2", "message 3", "message 4"]);
    expect(earlierHistory).toBe("none");
    expect(urls.filter(url => !url.includes("latest") && !url.includes("before")).every(url => url.includes("after=4"))).toBe(true);
  } finally { await act(async () => root.unmount()); node.remove(); }
});
