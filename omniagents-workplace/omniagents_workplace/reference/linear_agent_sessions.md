# Linear agent sessions: the types the workplace follows

Excerpted from `@linear/sdk` 99.0.0 (MIT, https://github.com/linear/linear), `dist/index-*.d.mts`.
Webhooks are signed: `linear-signature` is the hex HMAC-SHA256 of the raw body with the webhook's
secret, and the signed body's `webhookTimestamp` (ms) is checked for freshness (`dist/webhooks-*.mjs`).
An agent acknowledges a `created` session within 10 seconds with an activity (agentActivityCreate).

```ts
type AgentSessionEventWebhookPayload = {
  __typename?: "AgentSessionEventWebhookPayload";
  /** The type of action that triggered the webhook. */
  action: Scalars["String"];
  /** The agent activity that was created. */
  agentActivity?: Maybe<AgentActivityWebhookPayload>;
  /** The agent session that the event belongs to. */
  agentSession: AgentSessionWebhookPayload;
  /** ID of the app user the agent session belongs to. */
  appUserId: Scalars["String"];
  /** The time the payload was created. */
  createdAt: Scalars["DateTime"];
  /** Guidance to inform the agent's behavior, which comes from configuration at the level of the workspace, parent teams, and/or current team for this session. The nearest team-specific guidance should take highest precendence. */
  guidance?: Maybe<Array<GuidanceRuleWebhookPayload>>;
  /** ID of the OAuth client the app user is tied to. */
  oauthClientId: Scalars["String"];
  /** ID of the organization for which the webhook belongs to. */
  organizationId: Scalars["String"];
  /** The previous comments in the thread before this agent was mentioned and the session was initiated, if any. Present only for `created` events where the session was initiated by mentioning the agent in a child comment of a thread. */
  previousComments?: Maybe<Array<CommentChildWebhookPayload>>;
  /** A formatted prompt string containing the relevant context for the agent session, including issue details, comments, and guidance. Present only for `created` events. */
  promptContext?: Maybe<Scalars["String"]>;
  /** The type of resource. */
  type: Scalars["String"];
  /** The ID of the webhook that sent this event. */
  webhookId: Scalars["String"];
  /** Unix timestamp in milliseconds when the webhook was sent. */
  webhookTimestamp: Scalars["Float"];
};
```

```ts
type AgentSessionWebhookPayload = {
  __typename?: "AgentSessionWebhookPayload";
  /** The ID of the agent that the agent session belongs to. */
  appUserId: Scalars["String"];
  /** The time at which the entity was archived. */
  archivedAt?: Maybe<Scalars["String"]>;
  /** The root comment of the thread this agent session is attached to. */
  comment?: Maybe<CommentChildWebhookPayload>;
  /** The ID of the root comment of the thread this agent session is attached to. */
  commentId?: Maybe<Scalars["String"]>;
  /** The time at which the entity was created. */
  createdAt: Scalars["String"];
  /** The human user responsible for the agent session. Unset if the session was initiated via automation or by an agent user, with no responsible human user. */
  creator?: Maybe<UserChildWebhookPayload>;
  /** The ID of the human user responsible for the agent session. Unset if the session was initiated via automation or by an agent user, with no responsible human user. */
  creatorId?: Maybe<Scalars["String"]>;
  /** The time the agent session ended. */
  endedAt?: Maybe<Scalars["String"]>;
  /** The ID of the entity. */
  id: Scalars["String"];
  /** The issue this agent session is associated with. */
  issue?: Maybe<IssueWithDescriptionChildWebhookPayload>;
  /** The ID of the issue this agent session is associated with. */
  issueId?: Maybe<Scalars["String"]>;
  /** The ID of the organization that the agent session belongs to. */
  organizationId: Scalars["String"];
  /** The ID of the comment that this agent session was spawned from, if from a different thread. */
  sourceCommentId?: Maybe<Scalars["String"]>;
  /** Metadata about the external source that created this agent session. */
  sourceMetadata?: Maybe<Scalars["JSONObject"]>;
  /** The time the agent session started working. */
  startedAt?: Maybe<Scalars["String"]>;
  /** The current status of the agent session. */
  status: Scalars["String"];
  /** A summary of the activities in this session. */
  summary?: Maybe<Scalars["String"]>;
  /** The type of the agent session. */
  type: Scalars["String"];
  /** The time at which the entity was updated. */
  updatedAt: Scalars["String"];
  /** The URL of the agent session. */
  url?: Maybe<Scalars["String"]>;
};
```

```ts
type AgentActivityWebhookPayload = {
  __typename?: "AgentActivityWebhookPayload";
  /** The ID of the agent session that this activity belongs to. */
  agentSessionId: Scalars["String"];
  /** The time at which the entity was archived. */
  archivedAt?: Maybe<Scalars["String"]>;
  /** The content of the agent activity. */
  content: Scalars["JSONObject"];
  /** The time at which the entity was created. */
  createdAt: Scalars["String"];
  /** The ID of the entity. */
  id: Scalars["String"];
  /** An optional modifier that provides additional instructions on how the activity should be interpreted. */
  signal?: Maybe<Scalars["String"]>;
  /** Metadata about this agent activity's signal. */
  signalMetadata?: Maybe<Scalars["JSONObject"]>;
  /** The ID of the comment this activity is linked to. */
  sourceCommentId?: Maybe<Scalars["String"]>;
  /** The time at which the entity was updated. */
  updatedAt: Scalars["String"];
  /** The user who created this agent activity. */
  user: UserChildWebhookPayload;
  /** The ID of the user who created this agent activity. */
  userId: Scalars["String"];
};
```

```ts
type AgentActivityThoughtContent = {
  __typename?: "AgentActivityThoughtContent";
  /** The thought content in Markdown format. */
  body: Scalars["String"];
  /** [Internal] The thought content as ProseMirror document. */
  bodyData: Scalars["JSONObject"];
  /** The type of activity. */
  type: AgentActivityType;
};
```

```ts
type AgentActivityActionContent = {
  __typename?: "AgentActivityActionContent";
  /** The action being performed. */
  action: Scalars["String"];
  /** The parameters for the action, e.g. a file path, a keyword, etc. */
  parameter: Scalars["String"];
  /** The result of the action in Markdown format. */
  result?: Maybe<Scalars["String"]>;
  /** [Internal] The result content as ProseMirror document. */
  resultData?: Maybe<Scalars["JSONObject"]>;
  /** The type of activity. */
  type: AgentActivityType;
};
```

```ts
type AgentActivityResponseContent = {
  __typename?: "AgentActivityResponseContent";
  /** The response content in Markdown format. */
  body: Scalars["String"];
  /** [Internal] The response content as ProseMirror document. */
  bodyData: Scalars["JSONObject"];
  /** The type of activity. */
  type: AgentActivityType;
};
```

```ts
type AgentActivityElicitationContent = {
  __typename?: "AgentActivityElicitationContent";
  /** The elicitation message in Markdown format. */
  body: Scalars["String"];
  /** [Internal] The elicitation content as ProseMirror document. */
  bodyData: Scalars["JSONObject"];
  /** The type of activity. */
  type: AgentActivityType;
};
```

```ts
type AgentActivityErrorContent = {
  __typename?: "AgentActivityErrorContent";
  /** The error message in Markdown format. */
  body: Scalars["String"];
  /** [Internal] The error content as ProseMirror document. */
  bodyData: Scalars["JSONObject"];
  /** A stable reason code for specialized error handling. */
  reasonCode?: Maybe<Scalars["String"]>;
  /** The type of activity. */
  type: AgentActivityType;
};
```

```ts
type AgentActivityPromptContent = {
  __typename?: "AgentActivityPromptContent";
  /** A message requesting additional information or action from user. */
  body: Scalars["String"];
  /** [Internal] The prompt content as ProseMirror document. */
  bodyData: Scalars["JSONObject"];
  /** A short title capturing the intent of the prompt. */
  title?: Maybe<Scalars["String"]>;
  /** The type of activity. */
  type: AgentActivityType;
};
```

```ts
type AgentActivityCreateInput = {
  /** The agent session this activity belongs to. */
  agentSessionId: Scalars["String"];
  /**
   * The content payload of the agent activity. This object is not strictly typed.
   * See https://linear.app/developers/agent-interaction#activity-content-payload for typing details.
   */
  content: Scalars["JSONObject"];
  /** [Internal] Metadata about user-provided contextual information for this agent activity. */
  contextualMetadata?: InputMaybe<Scalars["JSONObject"]>;
  /** Whether the activity is ephemeral, and should disappear after the next activity. Defaults to false. */
  ephemeral?: InputMaybe<Scalars["Boolean"]>;
  /** The identifier in UUID v4 format. If none is provided, the backend will generate one. */
  id?: InputMaybe<Scalars["String"]>;
  /** An optional modifier that provides additional instructions on how the activity should be interpreted. */
  signal?: InputMaybe<AgentActivitySignal>;
  /** Metadata about this agent activity's signal. */
  signalMetadata?: InputMaybe<Scalars["JSONObject"]>;
};
```

```ts
type AgentSessionUpdateInput = {
  /** URLs of external resources to be added to this session. Only updatable by the OAuth application that owns the session. */
  addedExternalUrls?: InputMaybe<Array<AgentSessionExternalUrlInput>>;
  /** [Internal] The time at which the agent session was dismissed. Set to null to un-dismiss. Only updatable by internal clients. */
  dismissedAt?: InputMaybe<Scalars["DateTime"]>;
  /** The URL of an external agent-hosted page associated with this session. Only updatable by the OAuth application that owns the session. */
  externalLink?: InputMaybe<Scalars["String"]>;
  /** URLs of external resources associated with this session. Replaces existing URLs. Only updatable by the OAuth application that owns the session. If supplied, addedExternalUrls and removedExternalUrls are ignored. */
  externalUrls?: InputMaybe<Array<AgentSessionExternalUrlInput>>;
  /** A dynamically updated list of the agent's execution strategy. Only updatable by the OAuth application that owns the session. */
  plan?: InputMaybe<Scalars["JSONObject"]>;
  /** URLs to be removed from this session. Only updatable by the OAuth application that owns the session. */
  removedExternalUrls?: InputMaybe<Array<Scalars["String"]>>;
  /** The session title, displayed as its summary. Must contain 1 to 255 characters, including a non-whitespace character, with no line breaks or NUL bytes. Set to null to clear it and allow automatic title generation. Updating only this field does not acknowledge the session. Only updatable by the OAuth application that owns the session. */
  summary?: InputMaybe<Scalars["String"]>;
  /** [Internal] User-specific state for the agent session. Only updatable by internal clients. */
  userState?: InputMaybe<Array<AgentSessionUserStateInput>>;
};
```

```ts
declare enum AgentActivitySignal {
  Auth = "auth",
  Continue = "continue",
  Select = "select",
  Stop = "stop",
}
```

```ts
declare enum AgentSessionType {
  CommentThread = "commentThread",
}
```

```ts
declare enum AgentSessionStatus {
  Active = "active",
  AwaitingInput = "awaitingInput",
  Complete = "complete",
  Error = "error",
  Pending = "pending",
  Stale = "stale",
  Stopping = "stopping",
}
```
