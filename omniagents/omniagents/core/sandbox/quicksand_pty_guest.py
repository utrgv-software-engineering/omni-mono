"""Guest-only PTY bridge. Copied into the VM; never runs commands on the host.

A private Unix socket carries JSON control messages; terminal bytes use base64.
Each daemon owns one PTY/process group and a bounded output buffer.
"""

import base64
import fcntl
import json
import os
import pty
import selectors
import signal
import socket
import struct
import subprocess
import sys
import termios
import time


def serve(directory, config):
    os.setsid()
    os.umask(0o077)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(directory + "/control")
    listener.listen(8)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

    def child_setup():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    process = subprocess.Popen(
        ["/bin/sh", "-c", config["command"]],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        preexec_fn=child_setup,
        close_fds=True,
    )
    os.close(slave)
    os.set_blocking(master, False)
    events = selectors.DefaultSelector()
    events.register(master, selectors.EVENT_READ)
    events.register(listener, selectors.EVENT_READ)
    output = bytearray()
    pending_input = bytearray()
    deadline = time.monotonic() + config.get("timeout", 86400)
    last_client = time.monotonic()
    closed = False

    def terminate():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)

    try:
        while True:
            if time.monotonic() > deadline and process.poll() is None:
                terminate()
            if process.poll() is not None and time.monotonic() - last_client > 60:
                break
            for key, mask in events.select(0.05):
                if key.fileobj == master:
                    if mask & selectors.EVENT_WRITE:
                        try:
                            del pending_input[: os.write(master, pending_input)]
                        except BlockingIOError:
                            pass
                        if not pending_input:
                            events.modify(master, selectors.EVENT_READ)
                    if not mask & selectors.EVENT_READ:
                        continue
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        chunk = b""
                    if chunk:
                        output.extend(chunk)
                        if len(output) > 1024 * 1024:
                            del output[: -1024 * 1024]
                    else:
                        events.unregister(master)
                        closed = True
                    continue
                connection, _ = listener.accept()
                last_client = time.monotonic()
                with connection:
                    connection.settimeout(5)
                    raw = bytearray()
                    while b"\n" not in raw:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        raw.extend(chunk)
                        if len(raw) > 1024 * 1024:
                            raise ValueError("PTY request too large")
                    request = json.loads(raw)
                    operation = request["op"]
                    if operation == "write":
                        data = base64.b64decode(request["data"])
                        if len(pending_input) + len(data) > 1024 * 1024:
                            raise ValueError("PTY input buffer full")
                        pending_input.extend(data)
                        if not closed:
                            events.modify(
                                master, selectors.EVENT_READ | selectors.EVENT_WRITE
                            )
                    elif operation == "resize":
                        packed = struct.pack(
                            "HHHH",
                            max(1, min(65535, int(request["rows"]))),
                            max(1, min(65535, int(request["cols"]))),
                            0,
                            0,
                        )
                        fcntl.ioctl(master, termios.TIOCSWINSZ, packed)
                    elif operation == "terminate":
                        terminate()
                    elif operation != "read":
                        raise ValueError("Unknown PTY operation")
                    result = {"output": "", "exit_code": None}
                    if operation == "read":
                        result["output"] = base64.b64encode(output).decode()
                        output.clear()
                        result["exit_code"] = process.poll() if closed else None
                    connection.sendall(json.dumps(result).encode() + b"\n")
                    if operation == "terminate" or result["exit_code"] is not None:
                        return
    finally:
        terminate()
        events.close()
        listener.close()
        os.close(master)
        try:
            os.unlink(directory + "/control")
            os.rmdir(directory)
        except OSError:
            pass


def main():
    operation, directory, payload = sys.argv[1:]
    config = json.loads(base64.b64decode(payload))
    if operation == "start":
        if os.fork() == 0:
            null = os.open(os.devnull, os.O_RDWR)
            for fd in (0, 1, 2):
                os.dup2(null, fd)
            try:
                serve(directory, config)
            finally:
                os._exit(0)
        for _ in range(200):
            if os.path.exists(directory + "/control"):
                print("{}")
                return
            time.sleep(0.05)
        raise RuntimeError("PTY bridge did not start")
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(10)
        client.connect(directory + "/control")
        client.sendall(json.dumps(config).encode() + b"\n")
        with client.makefile("rb") as stream:
            print(stream.readline().decode(), end="")


if __name__ == "__main__":
    main()
