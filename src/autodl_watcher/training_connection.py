"""独占、可复用的只读 SSH 会话；仅回收本对象创建的进程。"""
from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import queue
import shlex
import shutil
import subprocess
import threading


def ssh_executable():
    path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/OpenSSH/ssh.exe" if os.name == "nt" else Path(shutil.which("ssh") or "/usr/bin/ssh")
    if not path.is_file():
        raise OSError(f"未找到OpenSSH客户端：{path}")
    return str(path)


class TrainingConnection:  # 每个训练监控保留自己的SSH进程；不借用或终止VS Code的连接。
    TIMEOUT = 35

    def __init__(self, server):
        self.server = server.validate()
        self.process = None
        self.closed = False
        self.attempts = self.samples = 0
        self._lifecycle = threading.RLock()
        self._request = threading.Lock()

    def _command(self):
        bootstrap = ('import json,sys;scope={"__name__":"readonly_probe"};'
                     'exec(compile(json.loads(sys.stdin.readline())["code"],"<readonly-training-probe>","exec"),scope)\n'
                     'for line in sys.stdin:\n'
                     ' print(json.dumps(scope["collect"](json.loads(line)),ensure_ascii=False),flush=True)')
        # MaxStartups的服务端横幅只在调试级别可见；stderr在内存限量读取，不显示密钥诊断明细。
        return [ssh_executable(), "-v", "-T", "-o", "BatchMode=yes", "-o", "PreferredAuthentications=publickey",
                "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
                "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                "-o", "StrictHostKeyChecking=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none",
                "-o", "PermitLocalCommand=no", self.server.ssh_alias,
                shlex.quote(self.server.python) + " -u -c " + shlex.quote(bootstrap)]

    def _open(self):
        with self._lifecycle:
            if self.closed:
                raise OSError("训练SSH连接已停止")
            if self.process is not None and self.process.poll() is not None:
                self._discard()
            if self.process is not None:
                return False
            source = Path(__file__).with_name("remote_probe.py").read_text(encoding="utf-8")
            self._bootstrap_message = json.dumps({"code": source}, ensure_ascii=False) + "\n"
            self.attempts += 1
            self.process = subprocess.Popen(self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True, encoding="utf-8", errors="replace", bufsize=1,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self._responses, self._errors = queue.Queue(), deque(maxlen=32)
            process, responses, errors = self.process, self._responses, self._errors
            def read_output():
                try:
                    for line in process.stdout:
                        responses.put(line)
                except (OSError, ValueError):
                    pass
                finally:
                    responses.put(None)
            def read_errors():
                try:
                    for line in process.stderr:
                        errors.append(line.rstrip())
                except (OSError, ValueError):
                    pass
            self._stderr_thread = threading.Thread(target=read_errors, daemon=True)
            self._stderr_thread.start()
            threading.Thread(target=read_output, daemon=True).start()
            return True

    def _error(self):  # 保留握手前的MaxStartups原因，不能只取最后一行“连接关闭”。
        detail = "\n".join(self._errors)
        if "MaxStartups" in detail:
            return "SSH接入被限流：未认证连接过多（MaxStartups）"
        if "Permission denied" in detail:
            return "SSH公钥认证失败，请检查SSH别名与密钥"
        if "Host key verification failed" in detail or "REMOTE HOST IDENTIFICATION HAS CHANGED" in detail:
            return "SSH主机身份核验失败，请核对服务器主机密钥"
        if "timed out" in detail.lower():
            return "SSH连接超时"
        return "SSH连接中断" + ("：" + self._errors[-1][:200] if self._errors else "")

    def collect(self, previous=()):
        with self._request:  # 同一会话只允许一问一答，避免不同轮次的响应串线。
            with self._lifecycle:
                fresh = self._open()
                process, responses = self.process, self._responses
            request = {"project": self.server.project, "scripts": [x.strip() for x in self.server.scripts.split(",") if x.strip()],
                       "previous": list(previous)}
            message = json.dumps(request, ensure_ascii=False) + "\n"
            if fresh:
                message = self._bootstrap_message + message
            def send():
                try:
                    process.stdin.write(message)
                    process.stdin.flush()
                except (OSError, ValueError):
                    responses.put(None)
            threading.Thread(target=send, daemon=True).start()  # 超时也覆盖握手期间阻塞的管道写入。
            try:
                line = responses.get(timeout=self.TIMEOUT)
                if line is None:
                    self._stderr_thread.join(timeout=1)
                    raise OSError(self._error())
                payload = json.loads(line)
                if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list) or not isinstance(payload.get("gpus"), list):
                    raise ValueError("服务器监控响应格式错误")
                self.samples += 1
                return payload
            except queue.Empty:
                self._discard()
                raise OSError("SSH采集超时，本次连接已关闭") from None
            except (OSError, ValueError):
                self._discard()
                raise

    def _discard(self):
        with self._lifecycle:
            process, self.process = self.process, None
            if process is None:
                return
            if process.poll() is None:
                process.terminate()  # 仅关闭此对象创建的本机ssh.exe；远端训练不是它的子进程。
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    def close(self):
        with self._lifecycle:
            self.closed = True
            self._discard()


def collect_remote(server, previous=()):  # 单次诊断入口；GUI使用TrainingConnection跨轮次复用。
    connection = TrainingConnection(server)
    try:
        return connection.collect(previous)
    finally:
        connection.close()
