import re
p = r"""D:\code\stardew-farm-bot\scripts\checksums.py"""
s = open(p, encoding="utf-8").read()

old = '''def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()'''
new = '''def sha256(path):
    """按 LF 归一化后算 sha256。

    这样 Windows（CRLF 检出）和 Linux（LF 检出）算出来一样，
    只有真正的内容变化才会被发现。
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        carry = b""
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            chunk = carry + chunk
            carry = b""
            if chunk.endswith(b"\\r"):
                carry = b"\\r"
                chunk = chunk[:-1]
            h.update(chunk.replace(b"\\r\\n", b"\\n"))
        if carry:
            h.update(carry)
    return h.hexdigest()'''
assert old in s
s = s.replace(old, new, 1)
open(p, "w", encoding="utf-8").write(s)
print("checksums.py: 加了 LF 归一化")