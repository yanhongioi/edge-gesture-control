from __future__ import annotations

import socket
import threading
import unittest

import numpy as np

from pc.audio.network_recorder import NetworkAudioStream
from pc.audio.recorder import AudioInputError


class NetworkAudioStreamTests(unittest.TestCase):
    def _server(self, payload: bytes) -> tuple[socket.socket, int, threading.Thread]:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)

        def send() -> None:
            client, _ = server.accept()
            with client:
                midpoint = len(payload) // 2
                client.sendall(payload[:midpoint])
                client.sendall(payload[midpoint:])

        thread = threading.Thread(target=send, daemon=True)
        thread.start()
        return server, server.getsockname()[1], thread

    def test_reads_fragmented_signed_pcm(self) -> None:
        samples = np.array([-32768, -16384, 0, 16384, 32767], dtype="<i2")
        server, port, thread = self._server(samples.tobytes())
        try:
            with NetworkAudioStream(
                "127.0.0.1", port=port, block_samples=len(samples)
            ) as stream:
                actual = stream.read_chunk()
            np.testing.assert_allclose(
                actual,
                samples.astype(np.float32) / 32768.0,
            )
        finally:
            server.close()
            thread.join(timeout=1)

    def test_closed_connection_is_reported(self) -> None:
        server, port, thread = self._server(b"")
        try:
            with NetworkAudioStream("127.0.0.1", port=port) as stream:
                with self.assertRaisesRegex(AudioInputError, "連線已中斷"):
                    stream.read_chunk()
        finally:
            server.close()
            thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
