# Security

This is an experimental local game agent. Security fixes target the current default branch.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/banjtheman/jev-brotato/security/advisories/new) when it is available. If that option is unavailable, open an issue asking for a private reporting channel without publishing exploit details or credentials. Include the affected commit, a minimal reproduction and the expected boundary when you report privately.

Never include a real `JEV_KEY`, `.env` contents, authorization headers or private game files in a report. Revoke an exposed key through its provider before sharing a sanitized reproduction.

## Local control boundary

The mod listens on `127.0.0.1:4243` and accepts one controller at a time. The protocol is unauthenticated: another process on the same machine can connect when the port is available and issue game actions. It is intended for a trusted local machine. Do not expose the port through forwarding, tunnels or a public listener.

Movement expires and is released on disconnect; observation IDs and menu validation reject stale actions. These checks constrain game commands, but they do not authenticate the client or isolate a malicious local process.

## Credentials and recorded data

The Python controller sends structured game state and candidate actions to TypeSafe over HTTPS. It reads `JEV_KEY` from the environment or the explicitly selected `.env` file. The supplied launcher removes `JEV_KEY` from the game process environment. The mod does not need the API credential.

Recordings can contain game captures, build information and decision history. Keep `.env`, local recordings, logs and extracted game files out of commits. The repository's ignore rules help prevent accidental additions; review staged files before publishing your own results.

## Automated checks

CI uses read-only repository permissions, pinned action commits and an empty `JEV_KEY`. Tests use synthetic fixtures and mocked API responses; bridge tests use loopback sockets. CI does not launch Brotato, connect to a running game or make TypeSafe API requests. Dependency installation requires network access.
