// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { MemoryApp } from "./Memory";
Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true});
it.each([true, false])("uses the same list/detail/editor flow (agent scope: %s)", async (agent) => {
 const memory={id:"m1",store:"agent:app/generalist",text:"Use staging before production",kind:"fact",tags:["deployment"],source:{},history:[{version:1,text:"Use staging before production"}]};
 const fetcher=vi.fn(async (url:string, _init?:RequestInit) => ({ok:true,json:async()=>url.includes("/stores") ? [{id:memory.store,name:"generalist",access:"write"}] : url.includes("/page?") ? {items:[memory],total:1,offset:0,limit:10} : url.includes("/search") ? [memory] : memory}));
 vi.stubGlobal("fetch",fetcher);
 const node=document.createElement("div");document.body.append(node);const root=createRoot(node);
 const button=(name:string)=>[...node.querySelectorAll("button")].find(b=>b.textContent===name || b.getAttribute("aria-label")===name)!;
 try {
  await act(async()=>root.render(<MemoryApp agent={agent} />));
  expect(node.querySelector("textarea")).toBeNull();
  expect(!!node.querySelector('[aria-label="Memory store"]')).toBe(false);
  expect(node.textContent).not.toContain(memory.store);
  expect(button(agent ? "New memory" : "Add memory to generalist")).toBeTruthy();
  await act(async()=>[...node.querySelectorAll("button")].find(b=>b.textContent?.includes(memory.text))!.click());
  expect(node.textContent).toContain("History");
  expect(node.querySelector('[aria-label="Search memories"]')).toBeNull();
  await act(async()=>button("Edit").click());
  expect(node.querySelector("textarea")!.value).toBe(memory.text);
  await act(async()=>node.querySelector("form")!.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true})));
  expect(fetcher.mock.calls.some(([url,init])=>url.endsWith(agent ? "/update?scope=agent" : "/update") && JSON.parse(init!.body as string).id==="m1")).toBe(true);
  expect(node.querySelector("textarea")).toBeNull();
  await act(async()=>button(agent ? "New memory" : "Add memory to generalist").click());
  expect(node.querySelector("textarea")!.value).toBe("");
  await act(async()=>button("Cancel").click());
  expect(node.querySelector('[aria-label="Search memories"]')).not.toBeNull();
 } finally {await act(async()=>root.unmount());node.remove();vi.unstubAllGlobals();}
});

it("keeps empty sections actionable and paginates collections independently", async () => {
 const stores=[{id:"personal:p",name:"Personal",access:"write"},{id:"agent:a/generalist",name:"Generalist",access:"write"},{id:"team",name:"Team",access:"read"}];
 const fetcher=vi.fn(async(url:string)=>({ok:true,json:async()=>{
  if(url.endsWith("/stores")) return stores;
  const params=new URL(url,"http://test").searchParams;
  const offset=Number(params.get("offset"));
  const populated=params.get("store")==="agent:a/generalist";
  return {total:populated?11:0,offset,limit:10,items:populated?Array.from({length:offset?1:10},(_,i)=>({id:String(offset+i),store:"agent:a/generalist",text:`Memory ${offset+i}`,kind:"fact",tags:[],source:{}})):[]};
 }}));
 vi.stubGlobal("fetch",fetcher);
 const node=document.createElement("div");document.body.append(node);const root=createRoot(node);
 try {
  await act(async()=>root.render(<MemoryApp/>));
  const personal=node.querySelector('section[aria-label="Personal"]')!;
  const team=node.querySelector('section[aria-label="Team"]')!;
  expect(personal.textContent).toContain("No memories yet.");
  expect(personal.querySelector('button[aria-label="Add memory to Personal"]')).not.toBeNull();
  expect(team.querySelector("button")).toBeNull();
  expect(node.querySelector('select[aria-label="Memory store"]')).toBeNull();
  const next=[...node.querySelectorAll('nav[aria-label="Generalist pages"] button')].find(b=>b.textContent==="Next")!;
  await act(async()=>next.dispatchEvent(new MouseEvent("click",{bubbles:true})));
  expect(node.textContent).toContain("11–11 of 11");
  expect(fetcher.mock.calls.some(([url])=>url.includes("offset=10"))).toBe(true);
  expect(personal.textContent).toContain("No memories yet.");
  await act(async()=>personal.querySelector("button")!.click());
  expect(node.querySelector("form")).not.toBeNull();
  expect(node.querySelector("section")!.textContent).toContain("Personal");
 } finally {await act(async()=>root.unmount());node.remove();vi.unstubAllGlobals();}
});
