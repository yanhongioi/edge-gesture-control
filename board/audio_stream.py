"""Stream an ALSA capture device to one TCP client as raw PCM audio."""

from __future__ import annotations

import argparse
import socket
import subprocess
from typing import BinaryIO


BYTES_PER_SAMPLE = 2


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    parts: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            return b""
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts)


def _capture_command(args: argparse.Namespace) -> list[str]:
    return [
        "arecord",
        "-q",
        "-D",
        args.device,
        "-f",
        "S16_LE",
        "-r",
        str(args.sample_rate),
        "-c",
        "1",
        "-t",
        "raw",
    ]


def _stream_client(client: socket.socket, args: argparse.Namespace) -> None:
    process = subprocess.Popen(
        _capture_command(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
    try:
        if process.stdout is None:
            raise RuntimeError("arecord stdout is unavailable")
        chunk_bytes = args.block_samples * BYTES_PER_SAMPLE
        while True:
            chunk = _read_exact(process.stdout, chunk_bytes)
            if not chunk:
                raise RuntimeError("arecord stopped producing audio")
            client.sendall(chunk)
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="0.0.0.0", help="listen address")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--device", default="hw:1,0", help="ALSA capture device")
    parser.add_argument("--sample-rate", type=int, default=16_000)
    parser.add_argument("--block-samples", type=int, default=512)
    parser.add_argument(
        "--allow-host",
        default="",
        help="only accept this client IP; empty accepts any reachable client",
    )
    args = parser.parse_args()

    if not 1 <= args.port <= 65_535:
        parser.error("--port must be between 1 and 65535")
    if args.sample_rate <= 0 or args.block_samples <= 0:
        parser.error("sample rate and block size must be positive")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((args.bind, args.port))
        server.listen(1)
        print(
            f"audio: {args.device} -> {args.bind}:{args.port} "
            f"({args.sample_rate} Hz, mono, S16_LE)",
            flush=True,
        )
        while True:
            client, address = server.accept()
            with client:
                if args.allow_host and address[0] != args.allow_host:
                    print(f"audio: rejected {address[0]}", flush=True)
                    continue
                print(f"audio: client connected from {address[0]}", flush=True)
                try:
                    _stream_client(client, args)
                except (BrokenPipeError, ConnectionError, RuntimeError) as exc:
                    print(f"audio: client disconnected ({exc})", flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\naudio: stopped")
