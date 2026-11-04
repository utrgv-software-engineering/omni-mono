# Approvals a person must give

Factories automate. A few actions are decisions about people (admitting an
applicant, changing a grade, booking or cancelling care), and a person must
make those. Two mechanisms keep them a person's:

1. **Credential scoping** (preferred). Where a decision belongs to a system of
   record, the factory's account there can prepare it (a draft, a pending item)
   but not finalize it; a person decides in that system. Nothing to configure in
   Workplace.
2. **Named tools.** When the factory itself carries out a decision, its
   `project.yml` names the tools:

   ```yaml
   security:
     human_approval_tools:
       - send_patient_message        # a tool's name
       - re:ehr_.*                   # a pattern over tool names
       - scheduling/cancel_visit     # an MCP server's tool
       - admissions/*                # all of an MCP server's tools
   ```

   The list is in the factory's repository, so changing it is a reviewed pull
   request, and a material change: under the strict policy the governance owner
   approves it.

## What a named tool does

Only it pauses; everything else runs as configured. Its approval is always a
person's: autopilot's allow-all pattern, "always allow", `skip_approvals`, the
Guardian reviewer and contained-sandbox auto-approval never cover it, and
approving it never grants it for later calls.

## The inbox

Approvals wait in the conversation where they were asked, and in the factory's
inbox (the Factory tab) with every approval from every chat and automation run.
The factory's administrators see the tool, the agent and the call's arguments,
and approve or decline. Each decision is audited on both sides with the
approver (`approval.decided` in Workplace; `tool_approved`/`tool_rejected` in
the runtime's audit, with `approver`).

New requests are posted to the Teams channel with the agent and the tool, never
the arguments.

## Restarts and expiry

The runtime keeps pending approvals in its event store. If the runtime restarts
while one waits, the run that asked for it has ended: the approval shows as
interrupted. Approving it starts a new turn in its conversation saying who
approved the call, with exactly that call (tool and arguments) approved once;
declining tells the conversation why. An approval no one answers within seven
days expires, declined.
