import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT_SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "webhook.sh"


class WebhookScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.scripts_dir = self.root / "scripts"
        self.bin_dir = self.root / "bin"
        self.venv_bin_dir = self.root / ".venv" / "bin"
        self.child_pid_file = self.root / "child.pid"

        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.venv_bin_dir.mkdir(parents=True, exist_ok=True)

        shutil.copy2(SCRIPT_SOURCE, self.scripts_dir / "webhook.sh")
        (self.root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")

        fake_python = self.venv_bin_dir / "python"
        fake_python.write_text(
            "\n".join([
                "#!/usr/bin/env bash",
                'port="8080"',
                "while [[ $# -gt 0 ]]; do",
                '  if [[ "$1" == "--port" ]]; then',
                '    port="$2"',
                "    shift 2",
                "    continue",
                "  fi",
                "  shift",
                "done",
                "exec python3 -c \"import os, socket, sys, time; "
                f"open(r'{self.child_pid_file}', 'w', encoding='utf-8').write(str(os.getpid())); "
                "sock = socket.socket(); "
                "sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); "
                "sock.bind(('0.0.0.0', int(sys.argv[1]))); "
                "sock.listen(); "
                "time.sleep(30)\" \"$port\"",
            ]) + "\n",
            encoding="utf-8",
        )
        fake_python.chmod(fake_python.stat().st_mode | stat.S_IEXEC)

        fake_uv = self.bin_dir / "uv"
        fake_uv.write_text(
            "\n".join([
                "#!/usr/bin/env bash",
                'if [[ "${1:-}" == "sync" ]]; then',
                "  exit 0",
                "fi",
                "exit 1",
            ]) + "\n",
            encoding="utf-8",
        )
        fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IEXEC)

        self.env = os.environ.copy()
        self.env["PATH"] = f"{self.bin_dir}:{self.env.get('PATH', '')}"

    def tearDown(self) -> None:
        if self.child_pid_file.exists():
            child_pid = int(self.child_pid_file.read_text(encoding="utf-8").strip())
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.temp_dir.cleanup()

    def _run_script(
        self,
        *args: str,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.scripts_dir / "webhook.sh"), *args],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=capture_output,
            timeout=30,
        )

    def _wait_for_child_pid(self) -> int:
        for _ in range(20):
            if self.child_pid_file.exists():
                return int(self.child_pid_file.read_text(encoding="utf-8").strip())
            time.sleep(0.2)
        self.fail("未等到 webhook 子进程 PID 文件生成")

    def _is_running(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        process = subprocess.run(
            ["ps", "-o", "state=", "-p", str(pid)],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        state = process.stdout.strip()
        if not state:
            return False
        return not state.startswith("Z")

    def test_stop_terminates_actual_webhook_process(self) -> None:
        start_result = self._run_script("start", "--port", "18080", capture_output=False)
        self.assertEqual(start_result.returncode, 0)

        child_pid = self._wait_for_child_pid()
        tracked_pid = int((self.root / "logs" / "webhook.pid").read_text(encoding="utf-8").strip())
        self.assertEqual(tracked_pid, child_pid)
        self.assertTrue(self._is_running(child_pid))

        stop_result = self._run_script("stop", capture_output=False)
        self.assertEqual(stop_result.returncode, 0)

        time.sleep(1)
        self.assertFalse(
            self._is_running(child_pid),
            f"stop 后真正的 webhook 进程仍然存活: child_pid={child_pid}, tracked_pid={tracked_pid}",
        )


if __name__ == "__main__":
    unittest.main()
