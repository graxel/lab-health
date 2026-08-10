import os
import socket
import subprocess
import psutil
from fastapi import FastAPI
import uvicorn

app = FastAPI()

def get_current_branch():
    try:
        head_file = os.path.join(".git", "HEAD")
        if os.path.exists(head_file):
            with open(head_file, "r") as f:
                ref = f.read().strip()
                if ref.startswith("ref:"):
                    return ref.split("/")[-1]
        return "unknown"
    except Exception:
        return "unknown"

def get_cpu_temp():
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for name, entries in temps.items():
                if entries:
                    return entries[0].current
        # Fallback for Raspberry Pi vcgencmd or sysfs thermal zone
        thermal_path = "/sys/class/thermal/thermal_zone0/temp"
        if os.path.exists(thermal_path):
            with open(thermal_path, "r") as f:
                return round(float(f.read().strip()) / 1000.0, 1)
        return None
    except Exception:
        return None

def check_systemd_service(service_name: str) -> dict:
    try:
        cmd = ["systemctl", "is-active", service_name]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        status = result.stdout.strip()
        return {
            "name": service_name,
            "status": status,
            "is_healthy": status == "active"
        }
    except Exception as e:
        return {
            "name": service_name,
            "status": "error",
            "is_healthy": False,
            "error": str(e)
        }

def get_docker_containers():
    containers = []
    try:
        import docker
        client = docker.from_env()
        for c in client.containers.list(all=True):
            containers.append({
                "name": c.name,
                "status": c.status,
                "is_healthy": c.status == "running"
            })
    except Exception:
        # Fallback to docker CLI if docker python SDK is missing
        try:
            cmd = ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                for line in res.stdout.strip().split("\n"):
                    if "|" in line:
                        name, status = line.split("|", 1)
                        is_running = "Up" in status
                        containers.append({
                            "name": name.strip(),
                            "status": status.strip(),
                            "is_healthy": is_running
                        })
        except Exception:
            pass
    return containers

def check_mount_point(path: str) -> bool:
    try:
        if not os.path.exists(path):
            return False
        # Verify writable/accessible
        return os.access(path, os.R_OK | os.W_OK)
    except Exception:
        return False

def check_tcp_reachability(host: str, port: int, timeout: int = 3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False

@app.get("/lab-health")
def health_check(services: str = ""):
    # CPU load average (1m, 5m, 15m)
    try:
        load1, load5, load15 = os.getloadavg()
    except Exception:
        load1, load5, load15 = psutil.cpu_percent(), 0.0, 0.0
        
    # Memory
    mem = psutil.virtual_memory()
    
    # Disk I/O
    disk_io = psutil.disk_io_counters()
    
    # Network I/O
    net_io = psutil.net_io_counters()

    # Systemd services check (passed as comma-separated query string)
    systemd_results = []
    if services:
        for svc in services.split(","):
            svc_clean = svc.strip()
            if svc_clean:
                systemd_results.append(check_systemd_service(svc_clean))
    
    # Check TCP reachability to icehouse Postgres (192.168.0.100:5432)
    can_reach_icehouse_postgres = check_tcp_reachability("192.168.0.100", 5432)
    
    return {
        "system_load": [load1, load5, load15],
        "cpu_temp": get_cpu_temp(),
        "mem_total": mem.total,
        "mem_used": mem.used,
        "mem_percent": mem.percent,
        "disk_read_bytes": disk_io.read_bytes if disk_io else 0,
        "disk_write_bytes": disk_io.write_bytes if disk_io else 0,
        "net_sent_bytes": net_io.bytes_sent if net_io else 0,
        "net_recv_bytes": net_io.bytes_recv if net_io else 0,
        "ssd_ok": check_mount_point("/mnt/ssd") if os.path.exists("/mnt/ssd") else True,
        "can_reach_icehouse_postgres": can_reach_icehouse_postgres,
        "systemd_services": systemd_results,
        "docker_containers": get_docker_containers(),
        "branch": get_current_branch()
    }

if __name__ == "__main__":
    uvicorn.run("reporter:app", host="0.0.0.0", port=8000, reload=True)
