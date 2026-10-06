"""install.sh and uninstall.sh (the all-in-one installer): questions, validation, the files it writes,
update on a second run, refusal of a 1.x directory, Docker missing, the embedded compose file.

Docker and curl are small fakes put first in PATH (as test_backup_remote.py fakes rclone), so nothing
needs Docker or the network. The scripts run in a new session, without a controlling terminal, and read
their answers from HSE_INSTALL_INPUT, so a test can never wait for a keyboard.

    python3 -m unittest tests.test_install
"""
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.sh"
UNINSTALL = ROOT / "uninstall.sh"
COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"

FAKE_DOCKER = r"""#!/bin/sh
echo "docker $* [cwd=$PWD]" >> "$FAKE_LOG"
if [ "$1 $2" = "compose version" ]; then
    if [ -n "$FAKE_NO_COMPOSE" ] && [ ! -f "$FAKE_STATE/installed" ]; then exit 1; fi
    echo "Docker Compose version v2.0.0"; exit 0
fi
case "$1" in
    info) if [ -n "$FAKE_DOCKER_DOWN" ]; then exit 1; fi; exit 0 ;;
    inspect) echo "${FAKE_HEALTH:-healthy}"; exit 0 ;;
    exec) echo "tok-0123456789abcdef"; exit 0 ;;
esac
exit 0
"""

# `curl ... | sh` of get.docker.com: the "script" it prints marks Docker as installed
FAKE_CURL = r"""#!/bin/sh
echo "curl $*" >> "$FAKE_LOG"
echo 'touch "$FAKE_STATE/installed"'
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        self.log = Path(self.tmp.name) / "docker.log"
        self.log.write_text("")
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        self.answers = Path(self.tmp.name) / "answers"
        self.answers.write_text("")

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HSE_", "ACME_", "FAKE_"))}
        env.update(PATH=f"{self.bin}:{env['PATH']}", FAKE_LOG=str(self.log), FAKE_STATE=str(self.state),
                   HSE_INSTALL_INPUT=str(self.answers), HSE_INSTALL_POLL="0", TZ="Europe/Madrid")
        env.update(extra)
        return env

    def run_script(self, script, *args, answers=None, stdin_script=False, **env):
        if answers is not None:
            self.answers.write_text("".join(a + "\n" for a in answers))
        cmd = ["bash", "-s", "--", *args] if stdin_script else ["bash", str(script), *args]
        return subprocess.run(cmd, cwd=self.work, env=self.env(**env), capture_output=True, text=True, timeout=60,
                              start_new_session=True, stdin=open(script) if stdin_script else subprocess.DEVNULL)

    def install(self, *args, **kw):
        return self.run_script(INSTALL, *args, **kw)

    def calls(self):
        return self.log.read_text().splitlines()

    def compose_calls(self):
        return [c for c in self.calls() if c.startswith("docker compose ") and "version" not in c]

    def dotenv(self, directory="headscale-easy"):
        out = {}
        for line in (self.work / directory / ".env").read_text().splitlines():
            if line and not line.startswith("#"):
                key, _, value = line.partition("=")
                out[key] = value
        return out


class Unattended(Base):
    def test_happy_path_writes_a_private_env_and_the_compose_file_and_starts_it(self):
        r = self.install("--yes", HSE_PUBLIC_URL="https://vpn.example.com", HSE_TLS="auto", ACME_EMAIL="me@example.com")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.work / "headscale-easy"
        self.assertEqual(stat.S_IMODE((d / ".env").stat().st_mode), 0o600)
        self.assertEqual(self.dotenv(), {"HSE_PUBLIC_URL": "https://vpn.example.com", "HSE_TLS": "auto",
                                         "ACME_EMAIL": "me@example.com", "TZ": "Europe/Madrid"})
        self.assertEqual((d / "docker-compose.yml").read_text(), COMPOSE.read_text())
        compose = self.compose_calls()
        self.assertEqual([c.split(" [")[0] for c in compose], ["docker compose pull", "docker compose up -d"])
        self.assertTrue(all(c.endswith(f"[cwd={d.resolve()}]") for c in compose), compose)
        self.assertIn("https://vpn.example.com/admin/setup", r.stdout)
        self.assertIn("One-time token: tok-0123456789abcdef", r.stdout)

    def test_the_directory_can_be_chosen(self):
        for args, env in ((["--dir", "mine"], {}), ([], {"HSE_DIR": "other"})):
            r = self.install("--yes", *args, HSE_PUBLIC_URL="http://192.168.1.10", **env)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.work / "mine" / ".env").is_file())
        self.assertTrue((self.work / "other" / ".env").is_file())

    def test_defaults_follow_the_address(self):
        cases = (("vpn.example.com", "auto"), ("192.168.1.10", "off"), ("localhost:8080", "off"), ("http://vpn.example.com", "off"))
        for i, (addr, tls) in enumerate(cases):
            r = self.install("--yes", "--dir", f"d{i}", HSE_PUBLIC_URL=addr)
            self.assertEqual(r.returncode, 0, (addr, r.stderr))
            self.assertEqual(self.dotenv(f"d{i}")["HSE_TLS"], tls, addr)

    def test_the_url_scheme_follows_the_https_mode(self):
        self.install("--yes", "--dir", "a", HSE_PUBLIC_URL="vpn.example.com", HSE_TLS="internal")
        self.assertEqual(self.dotenv("a")["HSE_PUBLIC_URL"], "https://vpn.example.com")
        self.install("--yes", "--dir", "b", HSE_PUBLIC_URL="192.168.1.10")
        self.assertEqual(self.dotenv("b")["HSE_PUBLIC_URL"], "http://192.168.1.10")
        r = self.install("--yes", "--dir", "c", HSE_PUBLIC_URL="https://vpn.example.com", HSE_TLS="off")
        self.assertEqual(self.dotenv("c")["HSE_PUBLIC_URL"], "https://vpn.example.com")  # a proxy terminates TLS
        self.assertIn("HSE_TRUSTED_PROXIES", r.stdout)
        self.assertIn("deploy/examples/front-proxy", r.stdout)
        self.assertIn("3478", r.stdout)

    def test_plain_http_is_a_warning(self):
        r = self.install("--yes", HSE_PUBLIC_URL="http://192.168.1.10")
        self.assertIn("unencrypted", r.stderr)

    def test_an_administrator_with_a_generated_password(self):
        r = self.install("--yes", HSE_PUBLIC_URL="http://192.168.1.10", HSE_ADMIN_EMAIL="admin@example.com")
        self.assertEqual(r.returncode, 0, r.stderr)
        env = self.dotenv()
        self.assertEqual(env["HSE_ADMIN_EMAIL"], "admin@example.com")
        self.assertRegex(env["HSE_ADMIN_PASSWORD"], r"^[A-Za-z0-9]{20}$")
        self.assertNotIn(env["HSE_ADMIN_PASSWORD"], r.stdout + r.stderr)  # it is in the .env, not on the screen
        self.assertNotIn(env["HSE_ADMIN_PASSWORD"], self.log.read_text())  # nor on any command line
        self.assertIn("http://192.168.1.10/admin as admin@example.com", r.stdout)
        self.assertNotIn("setup wizard", r.stdout.lower())

    def test_an_administrator_password_from_the_environment(self):
        self.install("--yes", HSE_PUBLIC_URL="http://192.168.1.10", HSE_ADMIN_EMAIL="a@example.com", HSE_ADMIN_PASSWORD="Chosen-Pass.9")
        self.assertEqual(self.dotenv()["HSE_ADMIN_PASSWORD"], "Chosen-Pass.9")

    def test_version_and_time_zone(self):
        self.install("--yes", HSE_PUBLIC_URL="http://192.168.1.10", HSE_VERSION="2.0.0", TZ="America/New_York")
        env = self.dotenv()
        self.assertEqual((env["HSE_VERSION"], env["TZ"]), ("2.0.0", "America/New_York"))
        self.install("--yes", "--dir", "x", HSE_PUBLIC_URL="http://192.168.1.10", TZ="bad tz;rm")
        self.assertEqual(self.dotenv("x")["TZ"], "UTC")

    def test_health_is_reported(self):
        r = self.install("--yes", HSE_PUBLIC_URL="http://192.168.1.10", FAKE_HEALTH="starting")
        self.assertEqual(r.returncode, 0)
        self.assertIn("is starting at", r.stdout)
        self.assertIn("not healthy yet", r.stderr)


class Validation(Base):
    def refuses(self, message, **env):
        r = self.install("--yes", **env)
        self.assertNotEqual(r.returncode, 0, (message, r.stdout))
        self.assertIn(message, r.stderr)
        self.assertFalse((self.work / "headscale-easy" / ".env").exists())
        self.assertEqual(self.compose_calls(), [])

    def test_no_address(self):
        self.refuses("an address is needed")

    def test_bad_addresses(self):
        for bad in ("vpn.example.com;rm -rf /", "a b", "$(id).example.com", "vpn.example.com/path", "ftp://x.example.com", "-bad.example.com", "x" * 5 + ":99999999"):
            self.refuses("not a valid address", HSE_PUBLIC_URL=bad)

    def test_bad_https_mode(self):
        self.refuses("HTTPS must be auto, internal or off", HSE_PUBLIC_URL="vpn.example.com", HSE_TLS="yes")

    def test_lets_encrypt_needs_a_name(self):
        for addr in ("192.168.1.10", "localhost"):
            self.refuses("Let's Encrypt does not issue", HSE_PUBLIC_URL=addr, HSE_TLS="auto", ACME_EMAIL="a@example.com")

    def test_bad_emails(self):
        self.refuses("not a valid email", HSE_PUBLIC_URL="vpn.example.com", HSE_TLS="auto", ACME_EMAIL="nope")
        self.refuses("not a valid email", HSE_PUBLIC_URL="http://192.168.1.10", HSE_ADMIN_EMAIL="a b@example.com")

    def test_bad_passwords(self):
        for bad in ("short", "has space 123456", 'quote"quote12345', "dollar$ign12345"):
            self.refuses("HSE_ADMIN_PASSWORD", HSE_PUBLIC_URL="http://192.168.1.10", HSE_ADMIN_EMAIL="a@example.com", HSE_ADMIN_PASSWORD=bad)

    def test_unknown_option_and_help(self):
        r = self.install("--nope")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unknown option", r.stderr)
        r = self.install("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--yes", r.stdout)
        self.assertEqual(self.compose_calls(), [])
        self.assertNotIn("docker", " ".join(self.calls()).replace("docker.log", ""))  # nothing ran


class Interactive(Base):
    def test_four_questions_at_most(self):
        # address, HTTPS, Let's Encrypt email, administrator email: the longest path is four answers
        r = self.install(answers=["vpn.example.com", "", "", "admin@example.com", "NOT-AN-ANSWER"])
        self.assertEqual(r.returncode, 0, r.stderr)
        env = self.dotenv()
        self.assertEqual((env["HSE_PUBLIC_URL"], env["HSE_TLS"], env["ACME_EMAIL"], env["HSE_ADMIN_EMAIL"]),
                         ("https://vpn.example.com", "auto", "admin@vpn.example.com", "admin@example.com"))
        self.assertNotIn("NOT-AN-ANSWER", (self.work / "headscale-easy" / ".env").read_text())

    def test_short_paths_ask_less(self):
        r = self.install(answers=["192.168.1.10", "", ""])  # off: no email question, so three answers
        self.assertEqual(r.returncode, 0, r.stderr)
        env = self.dotenv()
        self.assertEqual((env["HSE_TLS"], "ACME_EMAIL" in env), ("off", False))
        self.assertNotIn("HSE_ADMIN_EMAIL", env)  # the fourth line was the administrator question: left empty
        self.assertNotIn("SPARE", (self.work / "headscale-easy" / ".env").read_text())

    def test_answers_override_the_defaults(self):
        self.install(answers=["https://vpn.example.com", "internal", ""])
        env = self.dotenv()
        self.assertEqual((env["HSE_PUBLIC_URL"], env["HSE_TLS"]), ("https://vpn.example.com", "internal"))

    def test_the_script_can_arrive_on_stdin_like_curl_pipe_bash(self):
        r = self.run_script(INSTALL, "--dir", "piped", answers=["vpn.example.com", "internal", ""], stdin_script=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.dotenv("piped")["HSE_TLS"], "internal")  # answers came from the terminal, not from the script

    def test_no_terminal_means_defaults_and_not_a_hang(self):
        r = self.install(HSE_PUBLIC_URL="http://192.168.1.10", HSE_INSTALL_INPUT="/nonexistent")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no terminal", r.stderr)


class Existing(Base):
    def first(self, **env):
        r = self.install("--yes", HSE_PUBLIC_URL="https://vpn.example.com", HSE_TLS="internal", **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.log.write_text("")

    def test_no_pull_uses_the_image_that_is_already_here(self):
        r = self.install("--yes", "--no-pull", HSE_PUBLIC_URL="http://localhost", HSE_TLS="off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c.split(" [")[0] for c in self.compose_calls()], ["docker compose up -d"])
        again = self.install("--yes", "--no-pull", HSE_PUBLIC_URL="http://localhost", HSE_TLS="off")  # an update, too
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertNotIn("docker compose pull", [c.split(" [")[0] for c in self.compose_calls()[1:]])

    def test_a_second_run_only_updates_the_images(self):
        self.first(HSE_ADMIN_EMAIL="admin@example.com")
        env_file = self.work / "headscale-easy" / ".env"
        env_file.write_text(env_file.read_text() + "BACKUP_REMOTE=s3:bucket\n")
        before = env_file.read_bytes()
        r = self.install("--yes", HSE_PUBLIC_URL="https://other.example.com", HSE_ADMIN_EMAIL="x@example.com")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(env_file.read_bytes(), before)  # secrets and settings untouched, whatever the environment says
        self.assertEqual([c.split(" [")[0] for c in self.compose_calls()], ["docker compose pull", "docker compose up -d"])
        self.assertIn("Existing installation", r.stdout)
        self.assertFalse((self.work / "headscale-easy" / "docker-compose.yml.bak").exists())

    def test_a_second_run_asks_nothing(self):
        self.first()
        r = self.install(answers=["WRONG"])  # would break the .env if it were read
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.dotenv()["HSE_PUBLIC_URL"], "https://vpn.example.com")

    def test_a_changed_compose_file_is_replaced_and_kept_as_bak(self):
        self.first()
        compose = self.work / "headscale-easy" / "docker-compose.yml"
        compose.write_text("services: {}\n")
        r = self.install("--yes")
        self.assertEqual(compose.read_text(), COMPOSE.read_text())
        self.assertEqual((self.work / "headscale-easy" / "docker-compose.yml.bak").read_text(), "services: {}\n")
        self.assertIn("docker-compose.yml.bak", r.stdout)

    def test_data_is_never_deleted_by_an_update(self):
        self.first()
        self.install("--yes")
        self.assertFalse(any(" down" in c or " rm" in c or "volume" in c for c in self.calls()), self.calls())


class OneDotX(Base):
    def test_a_1x_directory_is_refused(self):
        for name, content in ((".env", "DOMAIN=vpn.example.com\nSSL_MODE=letsencrypt\n"), (".env", "AUTH_PROVIDER=none\n"),
                              ("headscale-config.yaml", "server_url: x\n")):
            d = self.work / "old"
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir()
            (d / name).write_text(content)
            before = sorted(p.name for p in d.iterdir())
            r = self.install("--yes", "--dir", "old", HSE_PUBLIC_URL="https://vpn.example.com", HSE_TLS="internal")
            self.assertNotEqual(r.returncode, 0, name)
            self.assertIn("1.x installation", r.stderr)
            self.assertIn("legacy/install-1x.sh", r.stderr)
            self.assertEqual(sorted(p.name for p in d.iterdir()), before)  # nothing written there
            self.assertEqual(self.compose_calls(), [])

    def test_a_new_directory_next_to_a_1x_one_is_fine(self):
        (self.work / ".env").write_text("SSL_MODE=letsencrypt\n")  # the repository root of a 1.x install
        r = self.install("--yes", HSE_PUBLIC_URL="http://192.168.1.10")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.work / ".env").read_text(), "SSL_MODE=letsencrypt\n")


class DockerNotThere(Base):
    def test_unattended_without_the_flag_does_not_install_it(self):
        r = self.install("--yes", HSE_PUBLIC_URL="http://192.168.1.10", FAKE_NO_COMPOSE="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Docker with the Compose plugin is required", r.stderr)
        self.assertFalse(any(c.startswith("curl") for c in self.calls()))
        self.assertFalse((self.work / "headscale-easy").exists())

    def test_unattended_with_the_flag_installs_it(self):
        r = self.install("--yes", "--install-docker", HSE_PUBLIC_URL="http://192.168.1.10", FAKE_NO_COMPOSE="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any(c.startswith("curl -fsSL https://get.docker.com") for c in self.calls()))
        self.assertTrue((self.work / "headscale-easy" / ".env").is_file())

    def test_interactive_asks_first(self):
        r = self.install(answers=["n"], FAKE_NO_COMPOSE="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(any(c.startswith("curl") for c in self.calls()))
        r = self.install(answers=["y", "192.168.1.10", "", ""], FAKE_NO_COMPOSE="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any(c.startswith("curl") for c in self.calls()))

    def test_a_daemon_that_does_not_answer(self):
        r = self.install("--yes", HSE_PUBLIC_URL="http://192.168.1.10", FAKE_DOCKER_DOWN="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot talk to Docker", r.stderr)
        self.assertEqual(self.compose_calls(), [])


class EmbeddedCompose(unittest.TestCase):
    """The installer carries the compose file inside itself (it must work from curl | bash)."""

    @staticmethod
    def embedded(text):
        m = re.search(r"<<'HSE_COMPOSE'\n(.*?)^HSE_COMPOSE\n", text, re.S | re.M)
        return m.group(1) if m else None

    def test_it_equals_deploy_compose(self):
        self.assertEqual(self.embedded(INSTALL.read_text()), COMPOSE.read_text(),
                         "run scripts/embed-compose.sh after editing deploy/compose/docker-compose.yml")

    def test_embedding_is_idempotent_and_follows_the_compose_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "deploy" / "compose").mkdir(parents=True)
            shutil.copy(ROOT / "scripts" / "embed-compose.sh", root / "scripts")
            shutil.copy(INSTALL, root / "install.sh")
            shutil.copy(COMPOSE, root / "deploy" / "compose" / "docker-compose.yml")
            before = (root / "install.sh").read_text()
            subprocess.run(["bash", str(root / "scripts" / "embed-compose.sh")], check=True)
            self.assertEqual((root / "install.sh").read_text(), before)
            (root / "deploy" / "compose" / "docker-compose.yml").write_text("services:\n  x:\n    image: y\n")
            subprocess.run(["bash", str(root / "scripts" / "embed-compose.sh")], check=True)
            self.assertEqual(self.embedded((root / "install.sh").read_text()), "services:\n  x:\n    image: y\n")

    def test_the_compose_file_is_safe_to_embed(self):
        self.assertNotIn("HSE_COMPOSE", COMPOSE.read_text())
        self.assertNotIn("docker.sock", COMPOSE.read_text())

    def test_the_script_is_small_and_valid(self):
        text = INSTALL.read_text()
        logic = text.replace(self.embedded(text), "")
        self.assertLessEqual(len(logic.splitlines()), 200)  # readable top to bottom
        self.assertEqual(subprocess.run(["bash", "-n", str(INSTALL)]).returncode, 0)
        self.assertEqual(subprocess.run(["bash", "-n", str(UNINSTALL)]).returncode, 0)


class Uninstall(Base):
    def installed(self):
        r = self.install("--yes", HSE_PUBLIC_URL="http://192.168.1.10")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.log.write_text("")

    def test_no_installation(self):
        r = self.run_script(UNINSTALL)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no installation", r.stderr)

    def test_plain_run_keeps_the_data(self):
        self.installed()
        r = self.run_script(UNINSTALL)
        self.assertEqual(r.returncode, 0, r.stderr)
        down = [c for c in self.compose_calls() if " down" in c]
        self.assertEqual(len(down), 1)
        self.assertNotIn("-v", down[0])
        self.assertIn("data is kept", r.stdout)

    def test_purge_asks_for_the_word_delete(self):
        self.installed()
        for answer in ("no", "delete", ""):
            r = self.run_script(UNINSTALL, "--purge", answers=[answer])
            self.assertNotEqual(r.returncode, 0, answer)
            self.assertIn("cancelled", r.stderr)
            self.assertEqual([c for c in self.compose_calls() if " down" in c], [])
        r = self.run_script(UNINSTALL, "--purge", answers=["DELETE"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any(" down -v" in c for c in self.compose_calls()))

    def test_purge_with_yes_does_not_ask(self):
        self.installed()
        r = self.run_script(UNINSTALL, "--purge", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(any(" down -v" in c for c in self.compose_calls()))

    def test_a_1x_directory_is_not_touched(self):
        d = self.work / "headscale-easy"
        d.mkdir()
        (d / "docker-compose.yml").write_text("services: {}\n")
        (d / ".env").write_text("SSL_MODE=letsencrypt\n")
        r = self.run_script(UNINSTALL, "--purge", "--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("legacy/uninstall-1x.sh", r.stderr)
        self.assertEqual(self.compose_calls(), [])


class Legacy(unittest.TestCase):
    def test_the_old_scripts_moved_and_still_parse(self):
        for name in ("install-1x.sh", "uninstall-1x.sh"):
            path = ROOT / "legacy" / name
            self.assertTrue(path.is_file(), name)
            self.assertEqual(subprocess.run(["bash", "-n", str(path)]).returncode, 0)
            self.assertIn('/.." && pwd)', path.read_text())  # the repository root is one level up
        self.assertIn("1.x installer", (ROOT / "legacy" / "install-1x.sh").read_text())

    def test_the_golden_generator_reads_the_moved_generators(self):
        text = (ROOT / "scripts" / "gen_render_goldens.sh").read_text()
        self.assertIn("legacy/install-1x.sh", text)
        self.assertNotIn('"$ROOT/install.sh"', text)
        legacy = (ROOT / "legacy" / "install-1x.sh").read_text()
        self.assertIn("\ndns_block() {", legacy)
        self.assertIn("# Headscale and the UI validate the OIDC issuer", legacy)


if __name__ == "__main__":
    unittest.main()
