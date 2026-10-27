// Getting into the workplace: setting a new one up, accepting an invite,
// signing in. Once signed in, the workplace itself (App).
import { useCallback, useEffect, useState } from "react";

import { Button } from "@omniagents/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@omniagents/ui/card";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@omniagents/ui/field";
import { Input } from "@omniagents/ui/input";
import { Toaster } from "@omniagents/ui/sonner";

import { auth, type Invite, type Person, type Session } from "./api";
import App from "./App";

export default function Root() {
  const [session, setSession] = useState<Session | null>(null);
  const [failed, setFailed] = useState("");
  const path = window.location.pathname;

  const load = useCallback(() => {
    auth.session().then(setSession, (e: Error) => setFailed(e.message));
  }, []);
  useEffect(load, [load]);

  const signedIn = (account: Person) => {
    window.history.replaceState(null, "", "/");
    setSession((s) => ({
      account,
      setup: false,
      organization: s?.organization ?? null,
    }));
    load();
  };

  if (failed)
    return <Centered>The workplace did not answer: {failed}</Centered>;
  if (!session) return <Centered>Connecting to the workplace…</Centered>;

  const setupToken = path.match(/^\/setup\/([^/]+)$/)?.[1];
  const inviteToken = path.match(/^\/invite\/([^/]+)$/)?.[1];
  if (inviteToken)
    return <InvitePage token={inviteToken} onSignedIn={signedIn} />;
  if (session.account)
    return (
      <App
        account={session.account}
        environment={session.environment ?? "development"}
        configurable={!!session.configurable}
        factory={!!session.factory}
        architecture={session.architecture ?? "specialist"}
        onSignOut={() => {
          void auth.signOut().then(load);
        }}
      />
    );
  if (session.claim_here)
    return <ClaimPage invite={session.claim_here} onSignedIn={signedIn} />;
  if (session.setup && (setupToken || session.setup_here))
    return (
      <SetupPage
        token={setupToken ?? ""}
        organization={session.organization}
        onSignedIn={signedIn}
      />
    );
  if (session.setup)
    return (
      <Page title="Set up the workplace">
        <CardContent className="text-sm text-muted-foreground">
          This workplace is new and needs setting up. On the computer it runs
          on, open it there. From anywhere else, use the setup link printed in
          the terminal where <code>omniagents-workplace serve</code> was
          started.
        </CardContent>
      </Page>
    );
  return (
    <SignInPage organization={session.organization} onSignedIn={signedIn} />
  );
}

/** This browser's time zone: the workplace shows its times in it. */
const browserTimeZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
      {children}
    </div>
  );
}

function Page({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-full items-center justify-center bg-muted/30 p-6">
      <Toaster />
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </CardHeader>
        {children}
      </Card>
    </div>
  );
}

function TextField({
  id,
  label,
  value,
  onChange,
  type = "text",
  description,
  autoComplete,
  placeholder,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  description?: string;
  autoComplete?: string;
  placeholder?: string;
}) {
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input
        id={id}
        type={type}
        value={value}
        autoComplete={autoComplete}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      {description && <FieldDescription>{description}</FieldDescription>}
    </Field>
  );
}

function useSubmit(run: () => Promise<Person>, onDone: (p: Person) => void) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    run().then(onDone, (err: Error) => {
      setError(err.message);
      setBusy(false);
    });
  };
  return { error, busy, submit };
}

function SignInPage({
  organization,
  onSignedIn,
}: {
  organization: string | null;
  onSignedIn: (p: Person) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const { error, busy, submit } = useSubmit(
    () =>
      auth.signIn(username, password, browserTimeZone()).then((r) => r.account),
    onSignedIn,
  );
  return (
    <Page
      title={organization ? `Sign in to ${organization}` : "Sign in"}
      description="An admin adds you and sends you an invite link."
    >
      <form onSubmit={submit}>
        <CardContent>
          <FieldGroup>
            <TextField
              id="username"
              label="Username"
              value={username}
              onChange={setUsername}
              autoComplete="username"
              placeholder="jsmith"
            />
            <TextField
              id="password"
              label="Password"
              type="password"
              value={password}
              onChange={setPassword}
              autoComplete="current-password"
              placeholder="••••••••"
            />
          </FieldGroup>
          {error && <p className="mt-3 text-sm text-destructive">{error}</p>}
        </CardContent>
        <CardFooter className="mt-4">
          <Button type="submit" className="w-full" disabled={busy}>
            Sign in
          </Button>
        </CardFooter>
      </form>
    </Page>
  );
}

function InvitePage({
  token,
  onSignedIn,
}: {
  token: string;
  onSignedIn: (p: Person) => void;
}) {
  const [invite, setInvite] = useState<Invite | null>(null);
  const [missing, setMissing] = useState("");
  useEffect(() => {
    auth.invite(token).then(setInvite, (e: Error) => setMissing(e.message));
  }, [token]);
  if (missing)
    return (
      <Page title="This invite link does not work">
        <CardContent className="text-sm text-muted-foreground">
          {missing}. Ask an admin for a new one.
        </CardContent>
      </Page>
    );
  if (!invite) return <Centered>Opening your invite…</Centered>;
  return (
    <ChoosePassword
      invite={invite}
      choose={(password) =>
        auth.acceptInvite(token, password, browserTimeZone())
      }
      onSignedIn={onSignedIn}
    />
  );
}

/** The admin of a workplace `up` made, on its machine: they choose their password. */
function ClaimPage({
  invite,
  onSignedIn,
}: {
  invite: Invite;
  onSignedIn: (p: Person) => void;
}) {
  return (
    <ChoosePassword
      invite={invite}
      choose={(password) => auth.claim(password, browserTimeZone())}
      onSignedIn={onSignedIn}
    />
  );
}

function ChoosePassword({
  invite,
  choose,
  onSignedIn,
}: {
  invite: Invite;
  choose: (password: string) => Promise<{ account: Person }>;
  onSignedIn: (p: Person) => void;
}) {
  const [password, setPassword] = useState("");
  const { error, busy, submit } = useSubmit(
    () => choose(password).then((r) => r.account),
    onSignedIn,
  );
  return (
    <Page
      title={`Join ${invite.organization}`}
      description={`You are ${invite.name} (${invite.username}). Choose a password to sign in with.`}
    >
      <form onSubmit={submit}>
        <CardContent>
          <TextField
            id="new-password"
            label="Password"
            type="password"
            value={password}
            onChange={setPassword}
            autoComplete="new-password"
            placeholder="••••••••"
            description="At least 8 characters."
          />
          {error && <p className="mt-3 text-sm text-destructive">{error}</p>}
        </CardContent>
        <CardFooter className="mt-4">
          <Button type="submit" className="w-full" disabled={busy}>
            Join
          </Button>
        </CardFooter>
      </form>
    </Page>
  );
}

function SetupPage({
  token,
  organization,
  onSignedIn,
}: {
  token: string;
  /** Named already (by the factory's workplace.yml): only its admin is asked for. */
  organization: string | null;
  onSignedIn: (p: Person) => void;
}) {
  const [form, setForm] = useState({
    organization: "",
    slug: "",
    username: "",
    name: "",
    email: "",
    password: "",
  });
  const [slugEdited, setSlugEdited] = useState(false);
  const set = (key: keyof typeof form) => (value: string) =>
    setForm((f) => {
      const next = { ...f, [key]: value };
      if (key === "organization" && !slugEdited)
        next.slug = value
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "");
      return next;
    });
  const { error, busy, submit } = useSubmit(
    () =>
      auth
        .setup({ token, ...form, tz: browserTimeZone() })
        .then((r) => r.account),
    onSignedIn,
  );
  return (
    <Page
      title={organization ? `Set up ${organization}` : "Set up the workplace"}
      description={
        organization
          ? "Make the first account: yours, an admin."
          : "Name your organization, and make the first account: yours, an admin."
      }
    >
      <form onSubmit={submit}>
        <CardContent>
          <FieldGroup>
            {!organization && (
              <>
            <TextField
              id="organization"
              label="Organization"
              value={form.organization}
              onChange={set("organization")}
              placeholder="Acme Inc."
            />
            <TextField
              id="slug"
              label="Short name"
              value={form.slug}
              onChange={(v) => {
                setSlugEdited(true);
                set("slug")(v);
              }}
              placeholder="acme"
              description="Its Slack domain, GitHub organization and Linear URL."
            />
              </>
            )}
            <TextField
              id="name"
              label="Your name"
              value={form.name}
              onChange={set("name")}
              autoComplete="name"
              placeholder="Jane Smith"
            />
            <TextField
              id="username"
              label="Username"
              value={form.username}
              onChange={set("username")}
              autoComplete="username"
              placeholder="jsmith"
            />
            <TextField
              id="email"
              label="Email"
              type="email"
              value={form.email}
              onChange={set("email")}
              autoComplete="email"
              placeholder="jane@example.com"
            />
            <TextField
              id="password"
              label="Password"
              type="password"
              value={form.password}
              onChange={set("password")}
              autoComplete="new-password"
              placeholder="••••••••"
              description="At least 8 characters."
            />
          </FieldGroup>
          {error && <p className="mt-3 text-sm text-destructive">{error}</p>}
        </CardContent>
        <CardFooter className="mt-4">
          <Button type="submit" className="w-full" disabled={busy}>
            Set up
          </Button>
        </CardFooter>
      </form>
    </Page>
  );
}
