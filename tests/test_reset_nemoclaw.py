import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
RESET = REPO / "scripts" / "reset-nemoclaw.sh"


class ResetNemoclawTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        self.state = Path(self.tmp.name) / "state.json"
        self.log = Path(self.tmp.name) / "commands.log"

    def make_commands(self, sandbox=True, destroy="success", remove_exit=0):
        self.state.write_text(json.dumps({
            "sandbox": sandbox,
            "destroy": destroy,
            "destroy_count": 0,
            "remove_exit": remove_exit,
        }))
        docker = self.bin / "docker"
        docker.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "args=sys.argv[1:]\n"
            "log=Path(os.environ['RESET_LOG'])\n"
            "with log.open('a') as f: f.write('docker ' + ' '.join(args) + '\\n')\n"
            "state=json.loads(Path(os.environ['RESET_STATE']).read_text())\n"
            "if args == ['info']: raise SystemExit(0)\n"
            "if args[:2] == ['container','inspect']:\n"
            "  ident=args[-1]\n"
            "  if ident == 'nemoclaw-vllm': raise SystemExit(1)\n"
            "  if len(args) > 2 and args[2] == '--format':\n"
            "    fmt=args[3]\n"
            "    if 'Config.Image' in fmt: print('')\n"
            "    elif 'Config.Cmd' in fmt: print('[]')\n"
            "    elif 'compose.service' in fmt: print('')\n"
            "    elif 'compose.project' in fmt: print('lab' if ident != 'unrelated' else 'other')\n"
            "    else: print('{}')\n"
            "  raise SystemExit(0)\n"
            "if args[:2] == ['ps','-aq'] and '--filter' not in args:\n"
            "  print('lab-running\\nlab-stopped\\none-off\\nunrelated')\n"
            "elif args[:2] == ['ps','-aq']:\n"
            "  print('lab-running\\nlab-stopped\\none-off')\n"
            "elif args[:3] == ['volume','ls','-q']: print('lab-volume')\n"
            "elif args[:3] == ['network','ls','-q']: print('lab-network')\n"
        )
        docker.chmod(0o755)
        nemoclaw = self.bin / "nemoclaw"
        nemoclaw.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "args=sys.argv[1:]\n"
            "log=Path(os.environ['RESET_LOG'])\n"
            "with log.open('a') as f: f.write('nemoclaw ' + ' '.join(args) + '\\n')\n"
            "state=json.loads(Path(os.environ['RESET_STATE']).read_text())\n"
            "if args == ['list']:\n"
            "  print('lab' if state['sandbox'] else '')\n"
            "elif args[1:] == ['destroy','--yes','--cleanup-gateway'] or args[1:] == ['destroy','--yes']:\n"
            "  state['destroy_count'] += 1\n"
            "  Path(os.environ['RESET_STATE']).write_text(json.dumps(state))\n"
            "  if state['destroy'] == 'repeat' or (state['destroy'] == 'incomplete' and state['destroy_count'] == 1):\n"
            "    print(\"MCP server 'incident' has an incomplete add transaction.\")\n"
            "    raise SystemExit(1)\n"
            "  if state['destroy'] == 'unknown':\n"
            "    print('destroy failed for unknown reason')\n"
            "    raise SystemExit(2)\n"
            "elif len(args) > 2 and args[1:3] == ['mcp','remove']:\n"
            "  if state['remove_exit']:\n"
            "    raise SystemExit(state['remove_exit'])\n"
            "elif len(args) > 1 and args[1] == 'status': pass\n"
        )
        nemoclaw.chmod(0o755)

    def run_reset(self, arguments, confirmation):
        env = os.environ.copy()
        env.update({
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "RESET_LOG": str(self.log),
            "RESET_STATE": str(self.state),
        })
        return subprocess.run(
            ["bash", str(RESET), *arguments],
            input=confirmation,
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=5,
        )

    def log_lines(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_confirmation_cancellation_changes_nothing(self):
        self.make_commands()
        result = self.run_reset(["--full"], "no\n")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(any(
            " start" in line or " destroy" in line or " rm" in line
            or " volume rm" in line or " network rm" in line
            for line in self.log_lines()
        ))

    def test_full_starts_retries_destroy_then_removes_lab_scope_in_order(self):
        self.make_commands(destroy="incomplete")
        mcp_state = self.home / ".local/state/nemoclaw-mcp/lab"
        mcp_state.mkdir(parents=True)
        (mcp_state / "state").write_text("temporary")
        (self.home / ".env").write_text("keep-env")
        cache = self.home / ".cache/huggingface"
        cache.mkdir(parents=True)
        (cache / "model.bin").write_text("keep-model")
        result = self.run_reset(["--full"], "RESET ALL lab\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self.log_lines()
        first_destroy = lines.index("nemoclaw lab destroy --yes --cleanup-gateway")
        mcp_remove = lines.index("nemoclaw lab mcp remove incident --force")
        second_destroy = lines.index("nemoclaw lab destroy --yes --cleanup-gateway", first_destroy + 1)
        self.assertLess(lines.index("nemoclaw lab start"), first_destroy)
        self.assertLess(first_destroy, mcp_remove)
        self.assertLess(mcp_remove, second_destroy)
        container_index = next(i for i, line in enumerate(lines) if "container rm -f lab-running" in line)
        volume_index = next(i for i, line in enumerate(lines) if "volume rm lab-volume" in line)
        network_index = next(i for i, line in enumerate(lines) if "network rm lab-network" in line)
        container_removals = [
            i for i, line in enumerate(lines) if "container rm -f " in line
        ]
        self.assertTrue(all(second_destroy < i < volume_index for i in container_removals))
        self.assertLess(container_index, volume_index)
        self.assertLess(volume_index, network_index)
        self.assertIn(
            "docker ps -aq --filter label=com.docker.compose.project=lab",
            lines,
        )
        self.assertIn(
            "docker volume ls -q --filter label=com.docker.compose.project=lab",
            lines,
        )
        self.assertIn(
            "docker network ls -q --filter label=com.docker.compose.project=lab",
            lines,
        )
        self.assertFalse(any("nemoclaw lab stop" in line for line in lines))
        self.assertTrue(any("container rm -f lab-stopped" in line for line in lines))
        self.assertTrue(any("container rm -f one-off" in line for line in lines))
        self.assertFalse(any("rm -f unrelated" in line for line in lines))
        self.assertFalse(mcp_state.exists())
        self.assertEqual((self.home / ".env").read_text(), "keep-env")
        self.assertEqual((cache / "model.bin").read_text(), "keep-model")

    def test_normal_reset_does_not_remove_lab_volumes(self):
        self.make_commands()
        result = self.run_reset([], "RESET lab\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any("volume rm" in line or "network rm" in line for line in self.log_lines()))

    def test_full_reset_without_sandbox_still_clears_lab_project(self):
        self.make_commands(sandbox=False)
        result = self.run_reset(["--full"], "RESET ALL lab\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self.log_lines()
        self.assertTrue(any("container rm -f lab-running" in line for line in lines))
        self.assertTrue(any("volume rm lab-volume" in line for line in lines))
        self.assertTrue(any("network rm lab-network" in line for line in lines))
        self.assertFalse(any("nemoclaw lab start" in line for line in lines))
        self.assertFalse(any("nemoclaw lab destroy" in line for line in lines))

    def test_destroy_error_stops_before_lab_deletion(self):
        self.make_commands(destroy="unknown")
        result = self.run_reset(["--full"], "RESET ALL lab\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("container rm" in line for line in self.log_lines()))

    def test_failed_mcp_remove_stops_before_lab_deletion(self):
        self.make_commands(destroy="incomplete", remove_exit=23)
        result = self.run_reset(["--full"], "RESET ALL lab\n")
        self.assertEqual(result.returncode, 23)
        lines = self.log_lines()
        self.assertIn("nemoclaw lab mcp remove incident --force", lines)
        self.assertFalse(any("container rm" in line for line in lines))
        self.assertFalse(any("volume rm" in line for line in lines))
        self.assertFalse(any("network rm" in line for line in lines))

    def test_repeated_incomplete_destroy_error_does_not_loop(self):
        self.make_commands(destroy="repeat")
        result = self.run_reset(["--full"], "RESET ALL lab\n")
        self.assertNotEqual(result.returncode, 0)
        destroys = [line for line in self.log_lines() if " destroy " in line]
        self.assertEqual(len(destroys), 2)
        self.assertEqual(
            self.log_lines().count("nemoclaw lab mcp remove incident --force"),
            1,
        )
        self.assertFalse(any("container rm" in line for line in self.log_lines()))


if __name__ == "__main__":
    unittest.main()
