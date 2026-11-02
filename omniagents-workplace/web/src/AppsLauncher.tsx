import { useState } from "react";
import { GitBranch, Hash, Mic, MoreHorizontal, Plug, Search, Sparkles, Pin, PinOff, MessageSquare, Factory, FolderOpen, LibraryBig, Monitor, Rocket, Archive } from "lucide-react";
import { Button } from "@omniagents/ui/button";
import { Input } from "@omniagents/ui/input";
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuTrigger } from "@omniagents/ui/dropdown-menu";
import { extensionRequest, type AppCatalog, type InstalledApp } from "./McpAppFrame";

export function AppsLauncher({catalog, refresh, navigate, manage}: {catalog: AppCatalog | null; refresh: () => Promise<void>; navigate: (path: string) => void; manage?: () => void}) {
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const entries = (catalog?.apps ?? []).filter(app => app.enabled).flatMap(app => app.entrypoints.map((entry,index) => ({app,entry,index}))).filter(({app,entry}) => `${app.name} ${entry.name}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const toggle = async (app: InstalledApp) => {
    setBusy(app.id);setError("");
    try {await extensionRequest(`/${app.id}/navigation`, {show_in_navigation: app.showInNavigation === false}, "PUT");await refresh();}
    catch(e) {setError((e as Error).message);}
    finally {setBusy(null);}
  };
  return <main className="workplace-page flex flex-1 flex-col gap-6 overflow-y-auto">
    <div className="flex flex-wrap items-center gap-3">
      <div className="relative min-w-0 flex-1 sm:max-w-sm"><Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input aria-label="Search apps" placeholder="Search apps" className="pl-9" value={search} onChange={e=>setSearch(e.target.value)} /></div>
      {manage && <Button variant="outline" className="ml-auto" onClick={manage}>Manage apps</Button>}
    </div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
      {entries.map(({app,entry,index}) => {
        const Icon = app.id === "native:personal" ? MessageSquare : app.id === "native:factory" ? Factory : app.id === "native:shared" ? FolderOpen : app.id === "native:library" ? LibraryBig : app.id === "native:desktop" ? Monitor : app.id === "native:coolify" ? Rocket : app.id === "native:memory" ? Archive : app.id === "slack" ? Hash : app.id === "github" ? GitBranch : app.id === "linear" ? Sparkles : app.id === "recorder" ? Mic : Plug;
        const image = entry.icons?.[0]?.src;
        return <div key={`${app.id}:${entry.tool}`} className="relative rounded-xl border bg-card">
          <Button variant="ghost" className="h-auto w-full flex-col gap-4 rounded-xl px-4 py-9" onClick={()=>navigate(app.path ?? `/apps/${app.id}/${index}`)}>
            <span className="flex size-14 items-center justify-center rounded-2xl bg-muted">{image?.match(/^(https:\/\/|data:image\/)/) ? <img src={image} alt="" className="size-8 object-contain" /> : <Icon className="size-7" />}</span>
            <span className="max-w-full truncate">{entry.name}</span>
          </Button>
          <DropdownMenu><DropdownMenuTrigger asChild><Button variant="ghost" size="icon-sm" className="absolute right-1 top-1" aria-label={`${entry.name} options`} disabled={busy === app.id}><MoreHorizontal /></Button></DropdownMenuTrigger><DropdownMenuContent align="end"><DropdownMenuGroup><DropdownMenuItem onSelect={()=>void toggle(app)}>{app.showInNavigation === false ? <Pin /> : <PinOff />}{app.showInNavigation === false ? "Show in navigation" : "Hide from navigation"}</DropdownMenuItem></DropdownMenuGroup></DropdownMenuContent></DropdownMenu>
        </div>;
      })}
    </div>
    {!catalog ? <p className="text-sm text-muted-foreground">Loading apps…</p> : entries.length === 0 && <p className="text-sm text-muted-foreground">{search ? "No apps match your search." : "No apps enabled."}</p>}
  </main>;
}
