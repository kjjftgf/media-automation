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
    # Demux: 8-byte header per frame
    out = ""
    i = 0
    while i + 8 <= len(raw):
        hdr = raw[i:i+8]
        size = int.from_bytes(hdr[4:8], 'big')
        i += 8
        out += raw[i:i+size].decode('utf-8', errors='replace')
        i += size
    return out

if __name__ == "__main__":
    c = sys.argv[1]
    cmd = sys.argv[2:]
    print(docker_exec(c, cmd))
