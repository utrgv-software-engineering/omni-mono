// The workplace's people: who is in it, and (for admins) adding people and
// handing them invite links. Also: changing your own password.
import { useEffect, useState } from "react";

import { Button } from "@omniagents/ui/button";
import { Checkbox } from "@omniagents/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@omniagents/ui/dialog";
import { Input } from "@omniagents/ui/input";
import { Label } from "@omniagents/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@omniagents/ui/table";
import { Copy } from "lucide-react";
import { toast } from "sonner";

import { auth, people as peopleApi, type Person } from "./api";

function InviteLink({ url, name }: { url: string; name: string }) {
  return (
    <div className="rounded-md border bg-muted/40 p-3 text-sm">
      <div className="mb-1 font-medium">
        {name}'s invite link (works once, for 7 days)
      </div>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate text-xs">{url}</code>
        <Button
          variant="outline"
          size="icon-sm"
          title="Copy"
          onClick={() =>
            void navigator.clipboard
              .writeText(url)
              .then(() => toast.success("Copied"))
          }
        >
          <Copy />
        </Button>
      </div>
    </div>
  );
}

export function PeopleDialog({
  open,
  onOpenChange,
  me,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  me: Person;
}) {
  const [list, setList] = useState<Person[]>([]);
  const [form, setForm] = useState({
    username: "",
    name: "",
    email: "",
    admin: false,
  });
  const [invite, setInvite] = useState<{ url: string; name: string } | null>(
    null,
  );
  const load = () =>
    void peopleApi.list().then(
      ({ people }) => setList(people),
      (e: Error) => toast.error(e.message),
    );
  useEffect(() => {
    if (open) load();
    else setInvite(null);
  }, [open]);

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    peopleApi.add(form).then(
      ({ person, invite_url }) => {
        setInvite({ url: invite_url, name: person.name });
        setForm({ username: "", name: "", email: "", admin: false });
        load();
      },
      (err: Error) => toast.error(err.message),
    );
  };
  const edit = (p: Person, body: Parameters<typeof peopleApi.edit>[1]) =>
    peopleApi.edit(p.id, body).then(load, (e: Error) => toast.error(e.message));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>People</DialogTitle>
          <DialogDescription>
            Everyone here is in Slack, GitHub and Linear, under the same
            username.
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-80 overflow-y-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Username</TableHead>
                <TableHead>Email</TableHead>
                {me.admin && <TableHead>Admin</TableHead>}
                {me.admin && <TableHead />}
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.map((p) => (
                <TableRow
                  key={p.id}
                  className={p.disabled ? "opacity-50" : undefined}
                >
                  <TableCell>
                    {p.name}
                    {me.admin && p.has_password === false && (
                      <span className="ml-2 text-xs text-muted-foreground">
                        invited
                      </span>
                    )}
                  </TableCell>
                  <TableCell>{p.username}</TableCell>
                  <TableCell>{p.email}</TableCell>
                  {me.admin && (
                    <TableCell>
                      <Checkbox
                        checked={p.admin}
                        disabled={p.id === me.id}
                        onCheckedChange={(v) => void edit(p, { admin: !!v })}
                      />
                    </TableCell>
                  )}
                  {me.admin && (
                    <TableCell className="space-x-1 text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={p.disabled}
                        onClick={() =>
                          void peopleApi.invite(p.id).then(
                            ({ invite_url }) =>
                              setInvite({ url: invite_url, name: p.name }),
                            (e: Error) => toast.error(e.message),
                          )
                        }
                      >
                        {p.has_password ? "Reset password" : "Invite link"}
                      </Button>
                      {p.id !== me.id && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() =>
                            void edit(p, { disabled: !p.disabled })
                          }
                        >
                          {p.disabled ? "Enable" : "Disable"}
                        </Button>
                      )}
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        {invite && <InviteLink url={invite.url} name={invite.name} />}
        {me.admin && (
          <form onSubmit={add} className="grid grid-cols-2 gap-3 border-t pt-4">
            <div className="col-span-2 text-sm font-medium">Add a person</div>
            <div className="space-y-1">
              <Label htmlFor="p-name">Name</Label>
              <Input
                id="p-name"
                placeholder="Jane Smith"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="p-username">Username</Label>
              <Input
                id="p-username"
                placeholder="jsmith"
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="p-email">Email</Label>
              <Input
                id="p-email"
                type="email"
                placeholder="jane@example.com"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </div>
            <div className="flex items-end gap-2 pb-2">
              <Checkbox
                id="p-admin"
                checked={form.admin}
                onCheckedChange={(v) => setForm({ ...form, admin: !!v })}
              />
              <Label htmlFor="p-admin">Admin</Label>
            </div>
            <div className="col-span-2 flex justify-end">
              <Button
                type="submit"
                disabled={!form.name || !form.username || !form.email}
              >
                Add and get an invite link
              </Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function PasswordDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    auth.changePassword(current, next).then(
      () => {
        toast.success("Password changed");
        setCurrent("");
        setNext("");
        onOpenChange(false);
      },
      (err: Error) => toast.error(err.message),
    );
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Change your password</DialogTitle>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="pw-current">Current password</Label>
            <Input
              id="pw-current"
              type="password"
              autoComplete="current-password"
              placeholder="••••••••"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="pw-new">New password</Label>
            <Input
              id="pw-new"
              type="password"
              autoComplete="new-password"
              placeholder="••••••••"
              value={next}
              onChange={(e) => setNext(e.target.value)}
            />
          </div>
          <div className="flex justify-end">
            <Button type="submit" disabled={!current || next.length < 8}>
              Change
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
