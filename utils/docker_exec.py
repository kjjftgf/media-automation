#!/usr/bin/env python3
import os
import json, sys, socket, http.client

SOCK = "/var/run/docker.sock"

def unix_req(method, path, body=None):
    conn = http.client.HTTPConnection("localhost")
    conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.sock.connect(SOCK)
    data = json.dumps(body).encode() if body else None
    h = {"Content-Type": "application/json"} if body else {}
    conn.request(method, path, body=data, headers=h)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return raw, resp.status

def docker_exec(container, cmd):
    # Create exec
    raw, status = unix_req("POST", f"/containers/{container}/exec", {
        "Cmd": cmd,
        "AttachStdout": True,
        "AttachStderr": True
    })
    if status != 201:
        return f"ERR create ({status}): {raw.decode()}"
    exec_id = json.loads(raw.decode()).get("Id")
    
    # Start exec - returns multiplexed stream
    raw, status = unix_req("POST", f"/exec/{exec_id}/start", {
        "Detach": False,
        "Tty": False
    })
    # ⚠️ start 失败时返回的是 JSON 错误体(如 404/409), 不是 multiplexed stream,
    #    必须先检查状态码, 否则会把错误 JSON 当流解析出乱码
    if status != 200:
        # 尝试解析 JSON 错误信息
        try:
            err = json.loads(raw.decode('utf-8', errors='replace'))
            return f"ERR start ({status}): {err.get('message', raw.decode(errors='replace'))}"
        except Exception:
            return f"ERR start ({status}): {raw.decode('utf-8', errors='replace')[:200]}"

    # Demux: 8-byte header per frame (stream_type[4] + size[4] big-endian)
    out = ""
    i = 0
    while i + 8 <= len(raw):
        hdr = raw[i:i+8]
        size = int.from_bytes(hdr[4:8], 'big')
        i += 8
        # ⚠️ 帧长校验: 防止截断帧或恶意 size 导致越界/静默损坏
        if size > len(raw) - i:
            # 数据不完整 - 保留已解析部分, 剩余字节丢弃
            break
        out += raw[i:i+size].decode('utf-8', errors='replace')
        i += size
    return out

if __name__ == "__main__":
    c = sys.argv[1]
    cmd = sys.argv[2:]
    print(docker_exec(c, cmd))
