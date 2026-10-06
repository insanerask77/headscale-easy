"""compose.yaml: the simple install is a Compose app of the all-in-one image.

One service, a pinned image, named volumes, no capability, no Docker socket; everything it
reads from .env is optional. The checks that need Docker (`docker compose config`) are skipped
without it.

    python3 -m unittest tests.test_compose
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
COMPOSE = os.path.join(ROOT, "compose.yaml")
ENV_EXAMPLE = os.path.join(ROOT, ".env.example")
HAVE_DOCKER = shutil.which("docker") is not None and subprocess.run(
    ["docker", "compose", "version"], capture_output=True).returncode == 0

sys.path.insert(0, os.path.join(ROOT, "aio"))
import render  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


VERSION = read(os.path.join(ROOT, "VERSION")).strip()  # the pinned image is the release's own version


def active_lines(text):
    return [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


class ComposeText(unittest.TestCase):
    def test_the_files_exist(self):
        self.assertTrue(os.path.exists(COMPOSE))
        self.assertTrue(os.path.exists(ENV_EXAMPLE))

    def test_the_image_is_pinned_to_a_2_0_version_and_never_latest(self):
        text = "\n".join(active_lines(read(COMPOSE)))
        m = re.search(r"image: ghcr\.io/insanerask77/headscale-easy:\$\{HSE_VERSION:-([^}]+)\}", text)
        self.assertIsNotNone(m, "the image must be ghcr.io/insanerask77/headscale-easy:${HSE_VERSION:-<version>}")
        self.assertRegex(m.group(1), r"^2\.\d+\.\d+$")
        self.assertNotIn("latest", text)

    def test_nothing_mounts_the_docker_socket(self):
        self.assertNotIn("docker.sock", "\n".join(active_lines(read(COMPOSE))))

    def test_hardening_options(self):
        text = "\n".join(active_lines(read(COMPOSE)))
        for option in ("cap_drop:", "- ALL", "no-new-privileges:true", "net.ipv4.ip_unprivileged_port_start: 0"):
            self.assertIn(option, text)
        self.assertNotIn("cap_add", text)
        self.assertNotRegex(text, r"(?m)^\s*privileged:")
        self.assertNotIn("network_mode", text)

    def test_one_service_and_the_ports(self):
        text = read(COMPOSE)
        services = text.split("\nvolumes:")[0].split("services:")[1]
        self.assertEqual(re.findall(r"(?m)^  ([a-z][a-z0-9-]*):\s*$", services), ["headscale-easy"])
        for port in ('"${HSE_DERP_PORT:-3478}:${HSE_DERP_PORT:-3478}/udp"', "${HSE_HTTP_PORT:-80}:80", "${HSE_HTTPS_PORT:-443}:443"):
            self.assertIn(port, text)

    def test_the_env_file_is_optional(self):
        self.assertRegex(read(COMPOSE), r"(?s)env_file:\s*\n\s*- path: \.env\s*\n\s*required: false")

    def test_env_example_sets_nothing_by_default(self):
        # `docker compose up -d` must work as shipped: every line of .env.example is a comment
        self.assertEqual(active_lines(read(ENV_EXAMPLE)), [])

    def test_env_example_documents_only_variables_the_image_reads(self):
        known = {name for name, _default in render.SETTINGS.values()} | {
            "HSE_VERSION", "HSE_HTTP_PORT", "HSE_HTTPS_PORT", "HSE_ADMIN_PASSWORD"}
        for name in re.findall(r"(?m)^#([A-Z][A-Z0-9_]+)=", read(ENV_EXAMPLE)):
            self.assertIn(name, known, "%s is not read by the image" % name)

    def test_the_commented_version_matches_the_pinned_one(self):
        pinned = re.search(r"\$\{HSE_VERSION:-([^}]+)\}", read(COMPOSE)).group(1)
        self.assertIn("#HSE_VERSION=%s" % pinned, read(ENV_EXAMPLE))


@unittest.skipUnless(HAVE_DOCKER, "needs Docker Compose")
class ComposeConfig(unittest.TestCase):
    def config(self, *overlays, env=None, dotenv=None):
        files = ("compose.yaml",) + overlays
        with tempfile.TemporaryDirectory() as tmp:
            for f in files:
                dest = os.path.join(tmp, f)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy(os.path.join(ROOT, f), dest)
            if dotenv is not None:
                with open(os.path.join(tmp, ".env"), "w", encoding="utf-8") as fh:
                    fh.write(dotenv)
            args = ["docker", "compose"]
            for f in files:
                args += ["-f", os.path.join(tmp, f)]
            clean = {k: v for k, v in os.environ.items() if not k.startswith(("HSE_", "COMPOSE_"))}
            res = subprocess.run(args + ["config", "--format", "json"], capture_output=True, text=True, cwd=tmp,
                                 env=dict(clean, **(env or {})))
            self.assertEqual(res.returncode, 0, res.stderr[-400:])
            return json.loads(res.stdout)

    def test_it_parses_without_an_env_file(self):
        cfg = self.config()
        self.assertEqual(list(cfg["services"]), ["headscale-easy"])
        self.assertEqual(cfg["services"]["headscale-easy"]["image"], f"ghcr.io/insanerask77/headscale-easy:{VERSION}")
        self.assertEqual(cfg["services"]["headscale-easy"]["container_name"], "headscale-easy")
        self.assertEqual(sorted(cfg["volumes"]), ["hse-backups", "hse-data"])

    def test_the_version_can_be_overridden(self):
        cfg = self.config(env={"HSE_VERSION": "2.0.1"})
        self.assertTrue(cfg["services"]["headscale-easy"]["image"].endswith(":2.0.1"))

    def test_the_env_file_reaches_the_container(self):
        cfg = self.config(dotenv="HSE_PUBLIC_URL=https://vpn.example.test\n")
        self.assertEqual(cfg["services"]["headscale-easy"]["environment"]["HSE_PUBLIC_URL"], "https://vpn.example.test")

    def test_the_container_is_hardened(self):
        svc = self.config()["services"]["headscale-easy"]
        self.assertEqual(svc["cap_drop"], ["ALL"])
        self.assertNotIn("cap_add", svc)
        self.assertIn("no-new-privileges:true", svc["security_opt"])
        self.assertFalse(svc.get("privileged"))
        self.assertTrue(all(v["type"] == "volume" for v in svc["volumes"]))

    def test_the_remote_backup_overlay_adds_the_sidecar(self):
        cfg = self.config("advanced/backup-remote.yaml")
        self.assertEqual(sorted(cfg["services"]), ["backup-remote", "headscale-easy"])
        self.assertTrue(cfg["services"]["backup-remote"]["image"].endswith(f":{VERSION}"))
        self.assertEqual(cfg["services"]["backup-remote"]["cap_drop"], ["ALL"])


if __name__ == "__main__":
    unittest.main()
