"""Offline transport tests plus one loopback TCP framing integration test."""

from io import BytesIO
import json
import queue
import socket
import threading
import unittest
from unittest.mock import patch

from brotato_agent.bridge import BridgeClient, BridgeError


class FakeSocket:
    def __init__(self, received):
        self.stream = BytesIO(received)
        self.sent = []
        self.closed = False

    def makefile(self, mode):
        return self.stream

    def sendall(self, data):
        if self.closed:
            raise OSError("closed")
        self.sent.append(data)

    def close(self):
        self.closed = True


def client_with(received):
    connection = FakeSocket(received)
    with patch("brotato_agent.bridge.socket.create_connection", return_value=connection):
        client = BridgeClient()
    return client, connection


class BridgeTests(unittest.TestCase):
    def test_requests_have_distinct_ids_and_movement_is_bound_to_observation(self):
        client, connection = client_with(b'{"id":1,"ok":true,"state":{"seq":91}}\n{"id":2,"ok":true}\n')
        self.assertEqual(client.observe(), {"seq": 91})
        client.move([0, -1], observation_seq=91, ttl_ms=350)
        messages = [json.loads(data) for data in connection.sent]
        self.assertEqual(messages[0], {"id": 1, "command": "observe"})
        self.assertEqual(messages[1], {
            "id": 2, "command": "move", "x": 0, "y": -1,
            "observation_seq": 91, "ttl_ms": 350,
        })
        self.assertTrue(all(message.endswith(b"\n") for message in connection.sent))
        client.close()

    def test_non_loopback_hosts_are_rejected_before_connecting(self):
        with patch("brotato_agent.bridge.socket.create_connection") as connect:
            with self.assertRaises(ValueError):
                BridgeClient(host="192.0.2.1")
        connect.assert_not_called()

    def test_invalid_json_disconnects(self):
        client, connection = client_with(b'{"bad":\n')
        with self.assertRaises(BridgeError):
            client.request("observe")
        self.assertTrue(connection.closed)
        self.assertTrue(connection.stream.closed)

    def test_bad_frames_and_mismatched_ids_disconnect(self):
        cases = [
            b"", b'{"id":1,"ok":true}', b"[]\n",
            b'{"id":2,"ok":true}\n', b'{"ok":true}\n',
            b" " * 1_048_577 + b"\n",
        ]
        for raw in cases:
            with self.subTest(prefix=raw[:40]):
                client, connection = client_with(raw)
                with self.assertRaises(BridgeError):
                    client.request("observe")
                self.assertTrue(connection.closed, "Invalid framing/IDs must close the connection")
                self.assertTrue(connection.stream.closed)

    def test_boolean_reply_id_is_not_a_request_sequence(self):
        client, connection = client_with(b'{"id":true,"ok":true}\n')
        with self.assertRaises(BridgeError):
            client.request("observe")
        self.assertTrue(connection.closed)

    def test_caller_cannot_override_sequence_id(self):
        client, connection = client_with(b'{"id":1,"ok":true}\n')
        with self.assertRaises((ValueError, BridgeError)):
            client.request("observe", id=88)
        self.assertEqual(connection.sent, [])
        client.close()

    def test_game_rejection_surfaces_the_error(self):
        client, connection = client_with(b'{"id":1,"ok":false,"error":"stale observation"}\n')
        with self.assertRaisesRegex(BridgeError, "stale observation"):
            client.move([1, 0], 10)
        client.close()

    def test_context_manager_releases_input_before_disconnect(self):
        client, connection = client_with(b'{"id":1,"ok":true}\n')
        with client:
            pass
        self.assertEqual(json.loads(connection.sent[0])["command"], "release")
        self.assertTrue(connection.closed)

    def test_loopback_tcp_stream_preserves_ndjson_boundaries(self):
        results = queue.Queue()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.settimeout(2)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        def server():
            try:
                with listener:
                    peer, _ = listener.accept()
                    with peer, peer.makefile("rb") as stream:
                        peer.settimeout(2)
                        requests = []
                        for _ in range(2):
                            request = json.loads(stream.readline())
                            requests.append(request)
                            reply = json.dumps({"id": request["id"], "ok": True, "state": {"seq": 10}}).encode() + b"\n"
                            # A JSON object may arrive in multiple TCP writes.
                            peer.sendall(reply[:7])
                            peer.sendall(reply[7:-1])
                            peer.sendall(reply[-1:])
                        results.put(requests)
            except Exception as exc:
                results.put(exc)

        worker = threading.Thread(target=server, daemon=True)
        worker.start()
        try:
            with BridgeClient(port=listener.getsockname()[1], timeout=2) as client:
                self.assertEqual(client.observe(), {"seq": 10})
        finally:
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive(), "Loopback server should finish")
        received = results.get(timeout=1)
        if isinstance(received, Exception):
            raise received
        self.assertEqual([request["command"] for request in received], ["observe", "release"])
        self.assertEqual([request["id"] for request in received], [1, 2])


if __name__ == "__main__":
    unittest.main()
