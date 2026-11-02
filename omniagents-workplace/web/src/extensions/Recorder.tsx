import { useEffect, useRef, useState } from "react";
import { Mic, Square, Pause, Play, Check, Trash2 } from "lucide-react";
import { Button } from "@omniagents/ui/button";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@omniagents/ui/alert-dialog";
import { Input } from "@omniagents/ui/input";
import { Field, FieldGroup, FieldLabel } from "@omniagents/ui/field";
import type { Person } from "../api";
import { appCall, base64, bridge } from "./main";

interface Recording { id: string; title: string; created: number; status: string; bytes: number; path: string }
function sessionTitle(date = new Date()) {
  return `${date.toLocaleDateString("en-US", { weekday: "long" })}, ${date.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true }).replace(/\s/g, "")}`;
}
export function Recorder(_props: { account: Person }) {
  const [discardTarget, setDiscardTarget] = useState<Recording | null>(null);
  const [discardBusy, setDiscardBusy] = useState(false);
  const [discardError, setDiscardError] = useState("");
  const [title, setTitle] = useState("");
  const [phase, setPhase] = useState<"idle" | "starting" | "recording" | "paused" | "ending" | "label" | "saving" | "discarding" | "retry">("idle");
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<Recording[]>([]);
  const media = useRef<MediaRecorder | null>(null);
  const current = useRef<Recording | null>(null);
  const queue = useRef<Blob[]>([]);
  const offset = useRef(0);
  const pending = useRef<Promise<void> | null>(null);
  const finishing = useRef(false);
  const stopped = useRef<Promise<void> | null>(null);
  const started = useRef(0);
  const recorded = useRef(0);

  const refresh = async () => {
    const data = await appCall<{ recordings: Recording[] }>("recorder.record", { action: "list" });
    setSaved(data.recordings.sort((a,b) => b.created-a.created));
  };
  useEffect(() => {
    void refresh().catch(e => setError(e.message));
    const warn = (event: BeforeUnloadEvent) => {
      if ((media.current && media.current.state !== "inactive") || queue.current.length) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", warn);
    const timer = setInterval(() => { if (media.current?.state === "recording") setElapsed(Math.floor((recorded.current+Date.now()-started.current)/1000)); }, 1000);
    bridge.onteardown = async () => { if (media.current && media.current.state !== "inactive") media.current.stop(); await stopped.current; return {}; };
    return () => { clearInterval(timer); window.removeEventListener("beforeunload", warn); };
  }, []);

  const flush = (): Promise<void> => {
    if (pending.current) return pending.current;
    const run = async () => {
      while (queue.current.length) {
        const blob = queue.current[0];
        const part = blob.slice(0, 65536);
        const data = base64(new Uint8Array(await part.arrayBuffer()));
        await appCall("recorder.record", { action: "write", id: current.current!.id, offset: offset.current, data });
        offset.current += part.size;
        if (blob.size > part.size) queue.current[0] = blob.slice(part.size); else queue.current.shift();
      }
    };
    pending.current = run().finally(() => { pending.current = null; });
    return pending.current;
  };
  const finish = async () => {
    if (finishing.current) return;
    finishing.current = true;
    setPhase("saving"); setError("");
    try {
      await flush();
      await appCall("recorder.record", { action: "finish", id: current.current!.id, title: title.trim() || current.current!.title });
      current.current = null; setPhase("idle"); setTitle(""); await refresh();
    } catch (e) { setError((e as Error).message); setPhase("retry"); }
    finally { finishing.current = false; }
  };
  const discard = async () => {
    if (finishing.current || !current.current) return;
    finishing.current = true;
    const previous = phase;
    setPhase("discarding"); setError("");
    try {
      await pending.current?.catch(() => {});
      await appCall("recorder.record", {action: "discard", id: current.current.id});
      queue.current = []; offset.current = 0; current.current = null; media.current = null;
      setTitle(""); setElapsed(0); setPhase("idle"); await refresh();
    } catch (e) { setError((e as Error).message); setPhase(previous); }
    finally { finishing.current = false; }
  };
  const start = async () => {
    setPhase("starting"); setError("");
    let stream: MediaStream | undefined;
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") throw new Error("Recording requires a supported browser and HTTPS.");
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"].find(type => MediaRecorder.isTypeSupported(type));
      if (!mimeType) throw new Error("This browser does not support an available audio recording format.");
      const recorder = new MediaRecorder(stream, { mimeType });
      current.current = await appCall<Recording>("recorder.record", { action: "start", title: sessionTitle(), mime: mimeType });
      queue.current = []; offset.current = 0; media.current = recorder;
      recorder.ondataavailable = event => {
        if (!event.data.size) return;
        queue.current.push(event.data);
        void flush().catch(e => {
          setError(`Audio upload paused: ${e.message}. Your unsent audio is still in this tab.`);
          if (recorder.state !== "inactive") recorder.stop();
        });
      };
      stopped.current = new Promise(resolve => {
        recorder.onstop = () => {
          recorder.stream.getTracks().forEach(track => track.stop());
          setPhase("ending");
          void flush().then(() => { setTitle(current.current!.title); setPhase("label"); }).catch(e => { setTitle(current.current!.title); setError(e.message); setPhase("label"); }).finally(resolve);
        };
      });
      recorder.onerror = () => { setError("The browser stopped recording. Saved audio can be recovered below."); if (recorder.state !== "inactive") recorder.stop(); };
      recorded.current = 0; started.current = Date.now(); setElapsed(0); recorder.start(2000); setPhase("recording");
    } catch (e) { stream?.getTracks().forEach(track => track.stop()); setError((e as Error).message); setPhase("idle"); }
  };
  const pause = () => {
    const recorder = media.current;
    if (!recorder) return;
    if (recorder.state === "recording") {
      recorded.current += Date.now() - started.current;
      recorder.pause(); recorder.requestData(); setElapsed(Math.floor(recorded.current / 1000)); setPhase("paused");
    } else if (recorder.state === "paused") {
      started.current = Date.now(); recorder.resume(); setPhase("recording");
    }
  };
  const end = () => {
    if (media.current?.state === "recording") recorded.current += Date.now() - started.current;
    setElapsed(Math.floor(recorded.current / 1000)); setPhase("ending"); media.current?.stop();
  };
  const active = phase === "recording" || phase === "paused";
  const naming = phase === "label" || phase === "retry" || phase === "saving" || phase === "discarding";
  const busy = phase === "saving" || phase === "discarding";
  return <main className="workplace-page flex flex-1 flex-col overflow-y-auto">
    <section className="flex min-h-[min(65dvh,36rem)] flex-col items-center justify-center gap-8 py-10" aria-label="Recorder">
      {naming ? <form className="flex w-full max-w-md flex-col gap-6" onSubmit={event => {event.preventDefault();void finish();}}>
        <FieldGroup><Field><FieldLabel htmlFor="recording-title">Session title</FieldLabel><Input id="recording-title" autoFocus value={title} onChange={e => setTitle(e.target.value)} disabled={busy} /></Field></FieldGroup>
        <div className="flex justify-end gap-3"><Button type="button" variant="ghost" disabled={busy} onClick={() => void discard()}>{phase === "discarding" ? "Discarding…" : "Discard"}</Button><Button type="submit" disabled={busy}>{phase === "saving" ? "Saving…" : phase === "retry" ? "Retry saving" : "Save recording"}</Button></div>
      </form> : <>
        <Button className="size-48 rounded-full sm:size-56" aria-label={active ? phase === "paused" ? "Resume recording" : "Pause recording" : "Start recording"} disabled={phase === "starting" || phase === "ending"} onClick={() => active ? pause() : void start()}>
          <span className="flex flex-col items-center gap-4">
            {active ? phase === "paused" ? <Play className="size-12" /> : <Pause className="size-12" /> : <Mic className="size-12" />}
            <span className="text-lg">{phase === "starting" ? "Starting…" : phase === "ending" ? "Ending…" : phase === "paused" ? "Resume" : phase === "recording" ? "Pause" : "Record"}</span>
          </span>
        </Button>
        {active && <div className="flex flex-col items-center gap-5">
          <span role="timer" className="text-3xl tabular-nums">{Math.floor(elapsed/60)}:{String(elapsed%60).padStart(2,"0")}</span>
          {phase === "paused" && <span className="text-sm text-muted-foreground">Paused</span>}
          <Button variant="outline" onClick={end}><Square data-icon="inline-start" />End recording</Button>
        </div>}
      </>}
      {error && <p role="alert" className="max-w-md text-sm text-destructive">{error}</p>}
    </section>
    {saved.filter(item => item.id !== current.current?.id).length > 0 && <section className="mx-auto flex w-full max-w-2xl flex-col" aria-label="Recordings">
      {saved.filter(item => item.id !== current.current?.id).map(item => <div key={item.id} className="flex items-center gap-3 border-b py-4">
        <div className="min-w-0 flex-1"><div className="truncate font-medium">{item.title}</div><div className="text-sm text-muted-foreground">{new Date(item.created*1000).toLocaleDateString()}{item.status !== "saved" && " · Unfinished"}</div></div>
        {item.status === "saved" ? <Check aria-label="Saved" className="size-4 text-muted-foreground" /> : <>
          <Button variant="outline" size="sm" disabled={phase !== "idle"} onClick={() => {current.current=item;offset.current=item.bytes;queue.current=[];setTitle(item.title === "Recording" ? sessionTitle(new Date(item.created*1000)) : item.title);setPhase("label");}}>Finish</Button>
          {item.status === "recording" && <Button variant="ghost" size="icon" disabled={phase !== "idle"} aria-label={`Discard ${item.title}`} onClick={() => {setDiscardError("");setDiscardTarget(item);}}><Trash2 /></Button>}
        </>}
      </div>)}
    </section>}
    <AlertDialog open={!!discardTarget} onOpenChange={open => {if (!open && !discardBusy) setDiscardTarget(null);}}>
      <AlertDialogContent>
        <AlertDialogHeader><AlertDialogTitle>Discard recording?</AlertDialogTitle><AlertDialogDescription>“{discardTarget?.title}” will be permanently deleted.</AlertDialogDescription></AlertDialogHeader>
        {discardError && <p role="alert" className="text-sm text-destructive">{discardError}</p>}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={discardBusy}>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={discardBusy} onClick={event => {
            event.preventDefault();
            if (!discardTarget || discardBusy) return;
            setDiscardBusy(true); setDiscardError("");
            void appCall("recorder.record", {action:"discard",id:discardTarget.id})
              .then(async () => {await refresh();setDiscardTarget(null);})
              .catch(e => setDiscardError(e.message))
              .finally(() => setDiscardBusy(false));
          }}>{discardBusy ? "Discarding…" : "Discard"}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </main>;
}
