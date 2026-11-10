# Kill switch

Any factory can be stopped at once, and an installation can stop every factory.
Texas requires that AI systems used by state agencies, including institutions of
higher education, "can be paused, restricted, or disabled until harmful or
inaccurate decision making can be remedied" (1 TAC §219.11(c)(2)(C)).

## Who can use it

| Switch | Stop | Resume |
| --- | --- | --- |
| One factory (**Stop factory** on the Factory tab) | The factory's administrators, installation administrators | The same, except that a stop made by an installation administrator needs one to resume |
| All factories (**Stop all factories** on the Factories page) | Installation administrators | Installation administrators |

Stopping and resuming both need a reason. Each is audited (`factory.stopped`,
`factory.resumed`, `installation.stopped`, `installation.resumed`) with who and
why. Stopping all factories does not change any factory's own stop; resuming all
returns each factory to its own state.

## What a stop does

- **Within seconds, all AI work ends.** The runtime cancels every run, worker and
  automation, removes queued messages, cancels events in progress and ends every
  command running in a sandbox. From that moment it starts no turn and makes no
  model or tool call.
- **While stopped:** chat refuses new messages and says who stopped the factory
  and why. Events Workplace's gateway would deliver, and events the runtime
  receives directly, are recorded as skipped. Scheduled automations are skipped.
- **The runtime shuts down** where the installation manages it: gracefully, so
  its sandbox VMs are suspended with their disks intact, and killed only after 60
  seconds, along with any of the factory's VMs still running. A stopped factory
  never starts, including after a restart.
- **Kept:** conversations, workspaces, configuration and files.
- **Not stopped:** preview applications and Actions runs, which are ordinary
  software; stop them separately.
- **On resume, nothing replays.** Skipped events stay skipped, removed queued
  work stays removed, and automations continue from their next scheduled time.

## How it is enforced

Workplace holds the state (the factory's `factory.stop`, the installation's
`installation.stop`) and treats the factory as stopped if it cannot read it. It
tells the runtime through `/integrations/stop` (the runtime's control token) and
keeps it in line every few seconds, so a runtime that restarts, or one Workplace
does not manage, stops too. The runtime keeps the stop in its own event store,
so it starts stopped after a restart even before Workplace reaches it, and it
checks the stop at every turn's admission and before every model and tool call.

## Out-of-band stops

If Workplace itself is unavailable, disable the model deployment's keys (every
agent loses its model at once) or stop the machine. Drill the switch every
quarter: stop one factory, stop all, resume, and confirm nothing replayed.
