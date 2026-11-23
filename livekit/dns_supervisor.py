"""Refresh LiveKit's announced ICE address when the site's DNS changes."""

import ipaddress
import logging
import os
import signal
import subprocess
import sys
import threading

LOG = logging.getLogger("livekit-dns")
POLL_INTERVAL = 15


def resolve_ipv4(name):
    result = subprocess.run(
        ["getent", "-s", "dns", "ahostsv4", name],
        capture_output=True, text=True, check=True, timeout=8,
    )
    return sorted({line.split()[0] for line in result.stdout.splitlines()}, key=ipaddress.IPv4Address)


def stop_child(process):
    if process is None or process.poll() is not None:
        return
    for _ in range(2):
        # A second SIGTERM tells LiveKit to stop even if a room is still active.
        process.terminate()
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
    process.kill()
    process.wait()


def supervise(name, args, stop):
    child = None
    active_ip = None
    dns_unavailable = False
    try:
        while not stop.is_set():
            if child is not None and (exit_code := child.poll()) is not None:
                LOG.error("LiveKit exited with status %s", exit_code)
                return exit_code or 1

            try:
                addresses = resolve_ipv4(name)
            except (OSError, subprocess.SubprocessError):
                addresses = []
            if not addresses:
                if not dns_unavailable:
                    LOG.warning("Could not resolve an IPv4 address for %s. Retrying.", name)
                dns_unavailable = True
            else:
                if active_ip not in addresses:
                    new_ip = addresses[0]
                    if active_ip is not None:
                        LOG.info("DNS changed for %s from %s to %s. Restarting LiveKit.", name, active_ip, new_ip)
                    stop_child(child)
                    if stop.is_set():
                        return 0
                    child = subprocess.Popen(["/usr/local/bin/livekit-server", "--node-ip", new_ip, *args])
                    LOG.info("Started LiveKit for %s at %s", name, new_ip)
                    active_ip = new_ip
                dns_unavailable = False

            stop.wait(POLL_INTERVAL)
        return 0
    finally:
        stop_child(child)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    sys.exit(supervise(os.environ["SERVER_NAME"], sys.argv[1:], stop))
