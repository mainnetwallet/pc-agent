"""Terminal control: PowerShell, CMD, Python, Node, Git, Docker, SSH, etc.

Executes commands with a timeout and returns exit code, stdout, stderr.
Never runs with elevated privileges unless explicitly requested.
"""

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from config import TASK_EXECUTION_TIMEOUT


def shutil_which(executable: str) -> Optional[str]:
    return shutil.which(executable)


@dataclass
class TerminalResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class TerminalControl:
    SHELLS = {
        "powershell": ["powershell.exe", "-NoProfile", "-Command"],
        "pwsh": ["pwsh.exe", "-NoProfile", "-Command"],
        "cmd": ["cmd.exe", "/c"],
        "python": ["python.exe"],
        "python3": ["python.exe"],
        "node": ["node.exe"],
        "npx": ["npx.cmd"],
        "git": ["git.exe"],
        "ssh": ["ssh.exe"],
    }

    def __init__(self, default_shell: str = "powershell"):
        self.default_shell = default_shell

    def _resolve_shell(self, shell: str) -> List[str]:
        shell = shell.lower()
        if shell in self.SHELLS:
            return list(self.SHELLS[shell])
        # Treat as a direct executable
        return [shell]

    def run(self, command: str, shell: str = "powershell",
            timeout: Optional[int] = None,
            cwd: Optional[str] = None,
            env: Optional[dict] = None) -> TerminalResult:
        """Run a command and capture output."""
        timeout = timeout or TASK_EXECUTION_TIMEOUT
        shell_cmd = self._resolve_shell(shell)

        if shell in ("powershell", "pwsh", "cmd"):
            # For PowerShell/CMD we pass the command as a single string argument
            full_cmd = shell_cmd + [command]
        else:
            # For python/node/git/etc. split the command into args
            try:
                args = shlex.split(command)
            except ValueError:
                args = [command]
            full_cmd = shell_cmd + args

        merged_env = dict(os.environ)
        if env:
            merged_env.update(env)

        start = time.time()
        try:
            proc = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=merged_env,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout.decode("utf-8", "replace") if e.stdout else ""
            stderr = e.stderr.decode("utf-8", "replace") if e.stderr else ""
            stderr += f"\n[Command timed out after {timeout}s]"
            exit_code = -1
        except FileNotFoundError:
            stdout = ""
            stderr = f"Executable not found: {shell_cmd[0]}"
            exit_code = 127
        except Exception as e:
            stdout = ""
            stderr = str(e)
            exit_code = 1
        duration = time.time() - start
        return TerminalResult(command, exit_code, stdout, stderr, duration)

    def run_powershell(self, command: str, **kwargs) -> TerminalResult:
        return self.run(command, shell="powershell", **kwargs)

    def run_cmd(self, command: str, **kwargs) -> TerminalResult:
        return self.run(command, shell="cmd", **kwargs)

    def run_python(self, command: str, **kwargs) -> TerminalResult:
        return self.run(command, shell="python", **kwargs)

    def run_node(self, command: str, **kwargs) -> TerminalResult:
        return self.run(command, shell="node", **kwargs)

    def run_git(self, command: str, **kwargs) -> TerminalResult:
        return self.run(command, shell="git", **kwargs)

    def which(self, executable: str) -> Optional[str]:
        return shutil_which(executable)


terminal = TerminalControl()
