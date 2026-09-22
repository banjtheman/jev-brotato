"""Synchronous, bounded NDJSON client for the local game mod."""
import json
import socket


class BridgeError(RuntimeError):
    pass


class BridgeRejected(BridgeError):
    """A well-formed game rejection; the transport remains usable."""


class BridgeClient:
    def __init__(self, host="127.0.0.1", port=4243, timeout=2.0):
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("The game bridge is restricted to localhost")
        self.socket = socket.create_connection((host, port), timeout=timeout)
        self.stream = self.socket.makefile("rb")
        self.sequence = 0

    def request(self, command, **kwargs):
        if "id" in kwargs:
            raise ValueError("Request id is managed by the client")
        self.sequence += 1
        payload = {"id": self.sequence, "command": command, **kwargs}
        try:
            self.socket.sendall((json.dumps(payload, allow_nan=False) + "\n").encode())
            line = self.stream.readline(1_048_577)
            if not line or len(line) > 1_048_576 or not line.endswith(b"\n"):
                raise BridgeError("Game bridge disconnected or exceeded message limit")
            reply = json.loads(line)
            if (not isinstance(reply, dict) or isinstance(reply.get("id"), bool)
                    or reply.get("id") != self.sequence):
                raise BridgeError("Game bridge response did not match the request")
            if reply.get("ok") is not True:
                raise BridgeRejected(str(reply.get("error", "Game bridge rejected request")))
            return reply
        except (OSError, ValueError) as exc:
            self.close()
            raise BridgeError("Game bridge transport failed; reconnect before retrying") from exc
        except BridgeRejected:
            raise
        except BridgeError:
            self.close()
            raise

    def observe(self):
        return self.request("observe")["state"]

    def move(self, vector, observation_seq, ttl_ms=500):
        return self.request("move", x=vector[0], y=vector[1],
                            observation_seq=observation_seq, ttl_ms=ttl_ms)

    def release(self):
        return self.request("release")

    def close(self):
        self.stream.close()
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        try:
            self.release()
        except (BridgeError, OSError, ValueError):
            pass
        self.close()
