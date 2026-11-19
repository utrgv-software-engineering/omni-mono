# GPT Live voice

`voice_backend: live` gives an agent GPT Live voice (`gpt-live-1`) using an
API key, through OpenAI or any OpenAI-compatible endpoint that serves Live
sessions:

```yaml
voice_backend: live
voice_instructions: Keep spoken replies to one or two sentences.
```

In Python:

```python
runner = Runner.from_agent(agent, voice_backend="live")
runner.run_notebook()
```

## How it works

The server holds the Live session at `<OPENAI_BASE_URL>/live/sessions`
(`wss://api.openai.com/v1/live/sessions` by default) and authenticates with
the API key, so the key never reaches the browser. The voice UI talks to the
server's `/ws/realtime` exactly as it does for realtime voice: 24 kHz PCM16
audio in and out, live captions, and spoken turns saved to the conversation.

The Live model speaks for the agent: its instructions are the agent's
instructions, a list of the agent's tools, and `voice_instructions`. It
handles conversation itself and delegates anything that needs a tool. Each
delegation is queued as a normal run of the agent — its tools, approvals and
history — and the run's result is handed back to the Live model to speak.
Live's delegation event carries no task text, so the delegated request is what
the user said since the previous delegation. The voice UI shows each
delegation as a step.

Typed messages are not accepted during a Live call (Live takes speech only);
type in the chat instead.

## Settings

| Setting | Environment variable | Default |
| --- | --- | --- |
| `openai_api_key` | `OPENAI_API_KEY` | required |
| `openai_base_url` | `OPENAI_BASE_URL` | `https://api.openai.com/v1` |
| `openai_live_model` | `OPENAI_LIVE_MODEL` | `gpt-live-1` |

Live sessions are billed per second of session time, including silence, so
stop voice when you are done.

The OpenAI-compatible gateway must forward the Live sessions WebSocket. A
LiteLLM proxy needs 1.110.1 or later, which serves `/v1/live/sessions` for the
`openai` provider.
