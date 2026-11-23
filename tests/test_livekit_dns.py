import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "livekit"))
import dns_supervisor as supervisor


class LiveKitDNSTest(unittest.TestCase):
    def test_restarts_after_the_active_dns_record_changes(self):
        name = "pyaterka.example.org"
        old_ip, new_ip = "192.0.2.10", "192.0.2.20"
        records = [[old_ip], [new_ip, old_ip], subprocess.CalledProcessError(2, "getent"), [new_ip]]
        stop = threading.Event()
        ticks = 0

        def poll_interval(_):
            nonlocal ticks
            ticks += 1
            if ticks == len(records):
                stop.set()

        old, new = Mock(), Mock()
        old.poll.return_value = None
        new.poll.return_value = None
        with (
            patch.object(stop, "wait", side_effect=poll_interval),
            patch.object(supervisor, "resolve_ipv4", side_effect=records),
            patch.object(supervisor.subprocess, "Popen", side_effect=[old, new]) as launch,
        ):
            self.assertEqual(supervisor.supervise(name, ["--config", "/etc/livekit.yaml"], stop), 0)
        self.assertEqual([call.args[0][1:] for call in launch.call_args_list], [
            ["--node-ip", old_ip, "--config", "/etc/livekit.yaml"],
            ["--node-ip", new_ip, "--config", "/etc/livekit.yaml"],
        ])
        old.terminate.assert_called_once()
        new.terminate.assert_called_once()

    def test_second_sigterm_allows_livekit_to_force_a_clean_exit(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("livekit-server", 5), 0]

        supervisor.stop_child(child)

        self.assertEqual(child.terminate.call_count, 2)
        child.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
