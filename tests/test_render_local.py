import contextlib
import io
import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
RENDERER_PATH = REPO / "scripts" / "render-local.py"
SPEC = importlib.util.spec_from_file_location("render_local", RENDERER_PATH)
assert SPEC is not None
render_local = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(render_local)


ENV = {
    "N8N_OWNER_PASSWORD_HASH": "owner-hash",
    "NEMOCLAW_MODEL": "nvidia/test-model",
    "NEMOCLAW_SPARK_SUPER_MODEL": "nvidia/super-model",
    "NEMOCLAW_VLLM_HF_MODEL": "nvidia/hf-model",
    "NEMOCLAW_SPARK_SUPER_VLLM_IMAGE": "test/vllm:latest",
}


class RenderLocalTests(unittest.TestCase):
    LOCAL_ENCRYPTION_KEY = "bt61191-local-lab-encryption-key"

    def render(self, target, mcp):
        output = self.fake_root / "generated"
        args = type("Args", (), {"target": target, "mcp": mcp})()
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(render_local, "ROOT", self.fake_root), \
                mock.patch.object(render_local, "SOURCE", self.fake_root / "cloud-init" / "user-data.yaml"), \
                mock.patch.object(render_local, "OUTPUT_ROOT", output), \
                mock.patch.object(render_local, "read_root_env", return_value=ENV):
            render_local.render(args)
        return output / target

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake_root = Path(self.tmp.name) / "repo"
        (self.fake_root / "cloud-init").mkdir(parents=True)
        shutil.copy2(
            REPO / "cloud-init" / "user-data.yaml",
            self.fake_root / "cloud-init" / "user-data.yaml",
        )
        shutil.copytree(REPO / "nemoclaw-mcp", self.fake_root / "nemoclaw-mcp")
        (self.fake_root / "scripts").mkdir()
        shutil.copy2(
            REPO / "scripts" / "reset-nemoclaw.sh",
            self.fake_root / "scripts" / "reset-nemoclaw.sh",
        )

    def test_all_targets_and_modes_render(self):
        for target in sorted(render_local.TARGETS):
            for mcp in ("skill", "none"):
                with self.subTest(target=target, mcp=mcp):
                    generated = self.render(target, mcp)
                    self.assertTrue((generated / "docker-compose.yml").is_file())
                    self.assertTrue((generated / "install-nemoclaw.sh").is_file())

    def test_skill_contains_exact_canonical_assets(self):
        generated = self.render("local", "skill")
        assets = generated / "nemoclaw-mcp"
        expected = {
            "install.sh",
            "policy/incident-mcp.yaml",
            "incident-mcp/SKILL.md",
            "incident-mcp/config.example.json",
            "incident-mcp/scripts/client.js",
        }
        actual = {
            str(path.relative_to(assets))
            for path in assets.rglob("*")
            if path.is_file()
        }
        self.assertEqual(expected, actual)
        for relative in expected:
            self.assertEqual(
                (assets / relative).read_bytes(),
                (REPO / "nemoclaw-mcp" / relative).read_bytes(),
            )

    def test_skill_scripts_are_executable_and_have_valid_bash(self):
        for target in sorted(render_local.TARGETS):
            for mcp in ("skill", "none"):
                generated = self.render(target, mcp)
                scripts = sorted(generated.rglob("*.sh"))
                with self.subTest(target=target, mcp=mcp):
                    self.assertGreaterEqual(len(scripts), 2)
                for script in scripts:
                    with self.subTest(target=target, mcp=mcp, script=script.relative_to(generated)):
                        self.assertTrue(script.stat().st_mode & 0o100)
                        result = subprocess.run(
                            ["bash", "-n", str(script)],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)

    def test_none_has_no_mcp_files_steps_or_token_requirement(self):
        generated = self.render("spark-super", "none")
        self.assertFalse((generated / "nemoclaw-mcp").exists())
        self.assertFalse((generated / "prepare-mcp.sh").exists())
        self.assertFalse((generated / "install-mcp.sh").exists())
        env_template = (generated / ".env.template").read_text()
        self.assertNotIn("N8N_MCP_TOKEN", env_template)
        install = (generated / "install-nemoclaw.sh").read_text()
        self.assertNotIn("install-mcp.sh", install)
        self.assertNotIn("prepare-mcp.sh", install)

    def test_cli_defaults_to_skill(self):
        with mock.patch.object(render_local, "render") as render, \
                mock.patch.object(sys, "argv", ["render-local.py", "--target", "local"]):
            render_local.main()
        args = render.call_args.args[0]
        self.assertEqual(args.target, "local")
        self.assertEqual(args.mcp, "skill")

    def test_rerender_preserves_existing_env_for_all_targets(self):
        saved = (
            "# User settings\n"
            "N8N_HOST_IP=192.168.50.7\n"
            "N8N_ENCRYPTION_KEY=custom-volume-key\n"
            "CUSTOM_SETTING=keep-me\n"
        ).encode()
        for target in sorted(render_local.TARGETS):
            with self.subTest(target=target):
                generated = self.render(target, "none")
                env_file = generated / ".env"
                env_file.write_bytes(saved)
                env_file.chmod(0o600)

                self.render(target, "none")

                self.assertEqual(env_file.read_bytes(), saved)
                self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)
                template = (generated / ".env.template").read_text()
                self.assertIn(
                    "N8N_ENCRYPTION_KEY=" + self.LOCAL_ENCRYPTION_KEY,
                    template,
                )
                self.assertNotIn("custom-volume-key", template)

    def test_rerender_keeps_previous_output_when_env_copy_fails(self):
        generated = self.render("local", "none")
        saved = b"N8N_HOST_IP=192.168.50.7\nN8N_ENCRYPTION_KEY=keep\n"
        (generated / ".env").write_bytes(saved)
        (generated / ".env").chmod(0o600)
        previous_run = (generated / "run.sh").read_bytes()

        original_copy2 = shutil.copy2

        def fail_env_copy(source, destination):
            if Path(source).name == ".env":
                raise PermissionError("test copy failure")
            return original_copy2(source, destination)

        with mock.patch.object(
            render_local.shutil,
            "copy2",
            side_effect=fail_env_copy,
        ):
            with self.assertRaises(PermissionError):
                self.render("local", "none")

        self.assertEqual((generated / ".env").read_bytes(), saved)
        self.assertEqual((generated / "run.sh").read_bytes(), previous_run)

    def write_fake_mcp_commands(self, root, bundled_exit=0):
        mcp = root / "nemoclaw-mcp"
        mcp.mkdir(exist_ok=True)
        installer = mcp / "install.sh"
        installer.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "test \"$HOST_IP\" = 192.168.50.7\n"
            "test -f \"$INCIDENT_MCP_TOKEN_FILE\"\n"
            "printf '%s' \"$INCIDENT_MCP_TOKEN_FILE\" > \"$HOME/token-path\"\n"
            f"{shlex.quote(sys.executable)} -c "
            "'import os, stat, sys; assert stat.S_IMODE(os.stat(sys.argv[1]).st_mode) == 0o600' "
            "\"$INCIDENT_MCP_TOKEN_FILE\"\n"
            "test \"$(cat \"$INCIDENT_MCP_TOKEN_FILE\")\" = dummy-token\n"
            "mkdir -p \"$HOME/.local/state/nemoclaw-mcp/lab\"\n"
            "printf '%s\\n' 'host: 192.168.50.7' > \"$HOME/.local/state/nemoclaw-mcp/lab/incident-mcp.yaml\"\n"
            f"exit {bundled_exit}\n"
        )
        installer.chmod(0o755)
        prepare = root / "prepare-mcp.sh"
        prepare.write_text("#!/usr/bin/env bash\nset -euo pipefail\nprintf prepare-ran > prepare.log\n")
        prepare.chmod(0o755)
        (root / ".env").write_text(
            "N8N_HOST_IP=192.168.50.7\nN8N_MCP_TOKEN=dummy-token\n"
        )
        bin_dir = Path(self.tmp.name) / "bin"
        bin_dir.mkdir(exist_ok=True)
        nemoclaw = bin_dir / "nemoclaw"
        nemoclaw.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf '%s\\n' \"$*\" > \"$HOME/policy-add.log\"\n"
        )
        nemoclaw.chmod(0o755)
        return bin_dir

    def run_install_mcp(self, bundled_exit=0):
        generated = self.render("local", "skill")
        prepare = generated / "prepare-mcp.sh"
        installer = generated / "nemoclaw-mcp" / "install.sh"
        prepare.unlink()
        installer.unlink()
        bin_dir = self.write_fake_mcp_commands(generated, bundled_exit)
        env = os.environ.copy()
        env.update({"HOME": self.tmp.name, "PATH": f"{bin_dir}:{env['PATH']}"})
        return generated, subprocess.run(
            ["bash", str(generated / "install-mcp.sh")],
            cwd=generated,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_install_mcp_applies_policy_with_host_and_cleans_token(self):
        generated, result = self.run_install_mcp()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((generated / "prepare.log").read_text(), "prepare-ran")
        policy_log = Path(self.tmp.name) / "policy-add.log"
        self.assertIn("--trusted-private-host 192.168.50.7", policy_log.read_text())
        self.assertIn("--from-file", policy_log.read_text())
        self.assertFalse(Path((Path(self.tmp.name) / "token-path").read_text()).exists())

    def test_spark_super_runs_ca_prepare_onboard_then_mcp_install(self):
        generated = self.render("spark-super", "skill")
        canonical_ca = "/usr/local/share/ca-certificates/caddy-lab-root.crt"
        (generated / ".env").write_text("NEMOCLAW_MODEL=nvidia/super-model\n")
        (generated / "prepare-mcp.sh").write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'prepare:%s\\n' \"${NEMOCLAW_CORPORATE_CA_BUNDLE-}\" >> \"$HOME/events\"\n"
        )
        (generated / "prepare-mcp.sh").chmod(0o755)
        (generated / "install-mcp.sh").write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'mcp-install:%s\\n' \"$NEMOCLAW_CORPORATE_CA_BUNDLE\" >> \"$HOME/events\"\n"
        )
        (generated / "install-mcp.sh").chmod(0o755)
        bin_dir = Path(self.tmp.name) / "bin"
        bin_dir.mkdir()
        nemoclaw = bin_dir / "nemoclaw"
        nemoclaw.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if [ \"${1-}\" = --version ]; then echo \"nemoclaw $NEMOCLAW_INSTALL_TAG\"; exit 0; fi\n"
            f"test \"$NEMOCLAW_CORPORATE_CA_BUNDLE\" = {canonical_ca}\n"
            "printf 'onboard:%s:%s\\n' \"$NEMOCLAW_CORPORATE_CA_BUNDLE\" \"$*\" >> \"$HOME/events\"\n"
        )
        nemoclaw.chmod(0o755)
        # Fail instead of downloading the real NemoClaw installer.
        (bin_dir / "curl").write_text("#!/usr/bin/env bash\necho 'curl blocked in tests' >&2\nexit 1\n")
        (bin_dir / "curl").chmod(0o755)
        env = os.environ.copy()
        env.update({"HOME": self.tmp.name, "PATH": f"{bin_dir}:{env['PATH']}"})
        result = subprocess.run(
            ["bash", str(generated / "install-nemoclaw.sh")],
            cwd=generated,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (Path(self.tmp.name) / "events").read_text().splitlines(),
            [
                "prepare:",
                f"onboard:{canonical_ca}:onboard --non-interactive --tool-disclosure direct",
                f"mcp-install:{canonical_ca}",
            ],
        )

    def test_local_existing_nemoclaw_onboards_before_mcp_install(self):
        generated = self.render("local", "skill")
        (generated / ".env").write_text(
            "N8N_HOST_IP=192.168.50.7\n"
            "N8N_MCP_TOKEN=dummy-token\n"
            "N8N_ENCRYPTION_KEY=test-key\n"
            "NEMOCLAW_MODEL=nvidia/test-model\n"
            "NVIDIA_INFERENCE_API_KEY=test-api-key\n"
        )
        (generated / "prepare-mcp.sh").write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
        )
        (generated / "prepare-mcp.sh").chmod(0o755)
        (generated / "install-mcp.sh").write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            "printf 'mcp-install\\n' >> \"$HOME/events\"\n"
        )
        (generated / "install-mcp.sh").chmod(0o755)
        bin_dir = Path(self.tmp.name) / "local-bin"
        bin_dir.mkdir()
        nemoclaw = bin_dir / "nemoclaw"
        nemoclaw.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            "if [ \"${1-}\" = --version ]; then echo \"nemoclaw $NEMOCLAW_INSTALL_TAG\"; exit 0; fi\n"
            "printf 'nemoclaw:%s\\n' \"$*\" >> \"$HOME/events\"\n"
        )
        nemoclaw.chmod(0o755)
        # Fail instead of downloading the real NemoClaw installer.
        (bin_dir / "curl").write_text("#!/usr/bin/env bash\necho 'curl blocked in tests' >&2\nexit 1\n")
        (bin_dir / "curl").chmod(0o755)
        env = os.environ.copy()
        env.update({"HOME": self.tmp.name, "PATH": f"{bin_dir}:{env['PATH']}"})
        result = subprocess.run(
            ["bash", str(generated / "install-nemoclaw.sh")],
            cwd=generated,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (Path(self.tmp.name) / "events").read_text().splitlines(),
            ["nemoclaw:onboard --non-interactive", "mcp-install"],
        )

    def test_install_mcp_does_not_apply_policy_after_bundled_failure(self):
        generated, result = self.run_install_mcp(bundled_exit=17)
        self.assertEqual(result.returncode, 17)
        self.assertFalse((Path(self.tmp.name) / "policy-add.log").exists())
        self.assertFalse(Path((Path(self.tmp.name) / "token-path").read_text()).exists())


if __name__ == "__main__":
    unittest.main()
