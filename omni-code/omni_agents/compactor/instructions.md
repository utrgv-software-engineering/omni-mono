The conversation has reached its context length limit and will be handed off to a new agent that will have NO knowledge of the prior conversation.

Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.

This summary must be detailed enough that the new agent can continue autonomously from exactly where work left off, without duplicating efforts. It should capture technical details, code patterns, architectural decisions, what was tried so far, what worked, and what did not work.

Before providing your final summary, include an <analysis> section.

The <analysis> section is a short coverage checklist only. It must NOT restate the summary in narrative form.

In <analysis>:
- Confirm you captured the user's explicit requests and constraints.
- Confirm you captured what was tried, what worked, and what failed.
- Confirm you captured exact file paths and key code identifiers (functions/classes) when present.
- Confirm you captured the last 5 tool calls and results (or explicitly state none were available).
- List any uncertainties or missing facts that the next agent must verify by re-reading files.

Your summary should include the following sections:
1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
5. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
6. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
7. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests without confirming with the user first.
8. If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:
<example>
<analysis>
[Coverage checklist only; no narrative duplication of <summary>.]
</analysis>
<summary>
1. Primary Request and Intent:
   [Detailed description]
2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]
3. Files and Code Sections:
   - [File Name 1]
     - [Summary of why this file is important]
     - [Summary of the changes made to this file, if any]
     - [Important Code Snippet]
   - [File Name 2]
     - [Important Code Snippet]
   - [...]
4. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]
5. Pending Tasks:
   - [Task 1]
   - [Task 2]
   - [...]
6. Current Work:
   [Precise description of current work]
7. Optional Next Step:
   [Optional Next step to take]
</summary>
</example>

Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response.
