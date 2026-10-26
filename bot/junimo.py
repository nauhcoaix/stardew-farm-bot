"""junimo-mcp 的 stdio 客户端：一个进程，一次一个请求。"""
import json
import os
import subprocess
import time

DEV = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(DEV, "junimo-mcp.exe")


class Junimo:
    def __init__(self):
        self.p = None
        self._id = 0

    def start(self):
        self.p = subprocess.Popen(
            [EXE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=DEV,
        )
        self._send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "dsh-farm", "version": "0.1"}}})
        self._recv(30)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        return self

    def alive(self):
        return self.p is not None and self.p.poll() is None

    def _send(self, obj):
        self.p.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def _recv(self, timeout=180):
        start = time.time()
        while time.time() - start < timeout:
            line = self.p.stdout.readline()
            if not line:
                return None
            txt = line.decode("utf-8", "replace").strip()
            if not txt:
                continue
            try:
                return json.loads(txt)
            except Exception:
                continue
        return None

    def call(self, name, args=None, timeout=180):
        if not self.alive():
            self.start()
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                    "params": {"name": name, "arguments": args or {}}})
        r = self._recv(timeout)
        if not r:
            return "（工具没有回应，可能超时）"
        if isinstance(r, dict) and r.get("error"):
            return "错误：" + json.dumps(r["error"], ensure_ascii=False)
        try:
            return r["result"]["content"][0]["text"]
        except Exception:
            return json.dumps(r, ensure_ascii=False)[:400]

    def close(self):
        try:
            if self.p:
                self.p.terminate()
        except Exception:
            pass
        self.p = None


if __name__ == "__main__":
    import sys
    j = Junimo().start()
    print(j.call(sys.argv[1] if len(sys.argv) > 1 else "get_state", {}))
    j.close()
