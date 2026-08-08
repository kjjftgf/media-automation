#!/usr/bin/env python3
"""
Docker容器健康监控脚本
通过 Docker socket 检查关键容器状态，异常时自动重启
"""
import os
import socket, json, http.client, sys, os

SOCK = "/var/run/docker.sock"
MONITORED = ["iptv", "cloud-auto-save-x", "cloudsaver", "searxng", "browser"]

def docker_api(method, path, body=None):
    conn = http.client.HTTPConnection("localhost")
    conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.sock.connect(SOCK)
    data = json.dumps(body).encode() if body else None
    h = {"Content-Type": "application/json"} if body else {}
    conn.request(method, path, body=data, headers=h)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return json.loads(raw.decode()) if raw else {}, resp.status

def check_and_restart():
    status = docker_api("GET", "/containers/json?all=true")
    containers = status[0] if isinstance(status, tuple) else status
    
    reports = []
    for c in containers:
        name = c.get("Names", [""])[0].lstrip("/")
        if name not in MONITORED:
            continue
        state = c.get("State", "unknown")
        cid = c.get("Id", "")[:12]
        
        if state == "running":
            # Check health if available
            insp, _ = docker_api("GET", f"/containers/{cid}/json")
            health = insp.get("State", {}).get("Health", {}).get("Status", "N/A")
            reports.append(f"OK  {name} (running, health={health})")
        else:
            # Restart
            docker_api("POST", f"/containers/{cid}/restart")
            reports.append(f"RESTARTED {name} (was {state})")
    
    return "\n".join(reports)

if __name__ == "__main__":
    print(check_and_restart())
