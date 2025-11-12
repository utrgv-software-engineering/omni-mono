# Remote Agent Example

This example runs an agent as a JSON‑RPC server and connects to it from another process.

## Files

- `server.py` – launches the remote server.
- `client.yml` – configuration for a client that connects to the server.

## Usage

Run the server:

```bash
python server.py
```

Then in another terminal run the client:

```bash
python -m omniagents.cli run -c client.yml
```
