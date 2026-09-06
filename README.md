# Paseo for Home Assistant

A local-push Home Assistant integration for watching a [Paseo](https://github.com/coreyja/paseo) agent fleet without putting prompts, working directories, or model output into Home Assistant's recorder.

## What it exposes

- Fleet counts for open sessions, active turns, idle, finished, attention, and failed agents
- A fleet-state sensor: `quiet`, `idle`, `working`, `attention`, or `error`
- Connection, attention, and failure binary sensors
- Per-provider open-session and active-turn counts
- Provider quota-window sensors with remaining percentage and reset time
- A safe event entity for agent started, finished, needs-input, and failed events

The integration connects directly to Paseo's WebSocket API and receives live updates. Usage data is refreshed every five minutes.

An **active turn** means Paseo is currently receiving output from an agent. It intentionally
returns to zero while every open agent is waiting for input or has finished, even when those
sessions remain open.

## Installation

In HACS, add `https://github.com/coreyja-studio/home-assistant-paseo` as a custom integration repository, install **Paseo**, and restart Home Assistant. Then add it from **Settings → Devices & services → Add integration → Paseo**.

Use the WebSocket URL reachable from Home Assistant, for example:

```text
wss://paseo-host.example.ts.net/ws
```

The password is optional and should only be entered if Paseo is configured to require one.

## Security and privacy

Paseo currently gives connected clients broad session authority. This integration only performs read operations, but the endpoint should remain on a trusted network such as Tailscale. Agent titles, prompts, working directories, tool calls, and output are deliberately discarded before data reaches Home Assistant entities or its recorder.

## Development

```bash
python -m pip install -r requirements-test.txt
ruff check .
pytest
```

## License

MIT
