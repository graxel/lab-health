import time
import json
import os
import socket
import ssl
from datetime import datetime, timezone
import requests
import psycopg
from dotenv import load_dotenv

def get_ssl_expiry_days(hostname: str, port: int = 443, timeout: int = 5) -> int:
    try:
        context = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                # cert['notAfter'] e.g. 'Aug 10 12:00:00 2026 GMT'
                expire_str = cert.get("notAfter")
                if expire_str:
                    expire_dt = datetime.strptime(expire_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
                    now_dt = datetime.now(timezone.utc)
                    return (expire_dt - now_dt).days
        return -1
    except Exception as e:
        print(f"[SSL Check] Error inspecting {hostname}:{port} -> {e}")
        return -1

def check_cors_preflight(url: str, origin: str = "https://kevingrazel.com", timeout: int = 5) -> dict:
    start_t = time.time()
    try:
        headers = {
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Content-Type"
        }
        resp = requests.options(url, headers=headers, timeout=timeout)
        r_time = int((time.time() - start_t) * 1000)
        allow_origin = resp.headers.get("Access-Control-Allow-Origin")
        is_cors_valid = (allow_origin == "*" or allow_origin == origin) or (resp.status_code in (200, 204) and allow_origin is not None)
        return {
            "status_code": resp.status_code,
            "response_time_ms": r_time,
            "allow_origin": allow_origin,
            "is_cors_valid": is_cors_valid
        }
    except Exception as e:
        r_time = int((time.time() - start_t) * 1000)
        return {
            "status_code": 0,
            "response_time_ms": r_time,
            "allow_origin": None,
            "is_cors_valid": False,
            "error": str(e)
        }

def check_hostname_resolution(hostname: str, expected_ip: str) -> dict:
    try:
        resolved_ip = socket.gethostbyname(hostname)
        is_match = (resolved_ip == expected_ip)
        return {
            "resolved_ip": resolved_ip,
            "expected_ip": expected_ip,
            "is_match": is_match
        }
    except Exception as e:
        return {
            "resolved_ip": None,
            "expected_ip": expected_ip,
            "is_match": False,
            "error": str(e)
        }

def monitor_icehouse_postgres_queries(local_conn, icehouse_ip="192.168.0.100", icehouse_port=5432):
    """Connect to icehouse Postgres to inspect heavy queries from pg_stat_statements"""
    icehouse_db_user = os.getenv("ICEHOUSE_DB_USER")
    icehouse_db_pass = os.getenv("ICEHOUSE_DB_PASSWORD")
    icehouse_db_name = os.getenv("ICEHOUSE_DB_NAME")
    
    if not (icehouse_db_user and icehouse_db_pass and icehouse_db_name):
        return
    
    conn_str = f"host={icehouse_ip} port={icehouse_port} dbname={icehouse_db_name} user={icehouse_db_user} password={icehouse_db_pass} connect_timeout=5"
    
    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                # Query pg_stat_statements if extension is installed, else query pg_stat_activity
                try:
                    cur.execute("""
                        SELECT queryid, datname, calls, total_exec_time, mean_exec_time, query 
                        FROM pg_stat_statements s
                        JOIN pg_database d ON d.oid = s.dbid
                        ORDER BY total_exec_time DESC LIMIT 5;
                    """)
                    rows = cur.fetchall()
                    with local_conn.cursor() as local_cur:
                        for row in rows:
                            local_cur.execute("""
                                INSERT INTO postgres_heavy_queries 
                                (query_id, datname, calls, total_exec_time_ms, mean_exec_time_ms, query_text)
                                VALUES (%s, %s, %s, %s, %s, %s)
                            """, (row[0], row[1], row[2], row[3], row[4], row[5]))
                except Exception:
                    # Fallback to pg_stat_activity for long running queries
                    cur.execute("""
                        SELECT pid, datname, 1, 
                               EXTRACT(EPOCH FROM (now() - query_start))*1000 AS exec_time,
                               EXTRACT(EPOCH FROM (now() - query_start))*1000 AS mean_time,
                               query
                        FROM pg_stat_activity 
                        WHERE state = 'active' AND query NOT LIKE '%pg_stat_activity%'
                        ORDER BY query_start ASC LIMIT 5;
                    """)
                    rows = cur.fetchall()
                    with local_conn.cursor() as local_cur:
                        for row in rows:
                            local_cur.execute("""
                                INSERT INTO postgres_heavy_queries 
                                (query_id, datname, calls, total_exec_time_ms, mean_exec_time_ms, query_text)
                                VALUES (%s, %s, %s, %s, %s, %s)
                            """, (row[0], row[1], row[2], row[3], row[4], row[5]))
        print("[Icehouse Postgres] Heavy query check complete.")
    except Exception as e:
        print(f"[Icehouse Postgres] Could not query icehouse database: {e}")

def monitor_loop():
    load_dotenv("settings.env")
    load_dotenv("secrets.env")
    
    poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
    
    conn_info = f"""
        host={os.getenv("DB_HOST")} 
        port={os.getenv("DB_PORT")} 
        dbname={os.getenv("DB_NAME")} 
        user={os.getenv("DB_USER")} 
        password={os.getenv("DB_PASSWORD")}
    """

    while True:
        print("Polling lab-health network...")
        if not os.path.exists("machines.json"):
            print("machines.json missing!")
            time.sleep(poll_interval)
            continue

        with open("machines.json", "r") as f:
            data = json.load(f)
            
        try:
            with psycopg.connect(conn_info, autocommit=True) as conn:
                for machine in data.get("machines", []):
                    ip = machine.get("ip")
                    name = machine.get("name")
                    hostname = machine.get("hostname")
                    env = machine.get("environment", "prod")
                    projects = machine.get("projects", [])
                    
                    # 1. Upsert machine entry
                    with conn.cursor() as cur:
                        cur.execute("SELECT id FROM machines WHERE name = %s", (name,))
                        res = cur.fetchone()
                        if res:
                            machine_id = res[0]
                            cur.execute("UPDATE machines SET ip=%s, hostname=%s, environment=%s WHERE id=%s", (ip, hostname, env, machine_id))
                        else:
                            cur.execute(
                                "INSERT INTO machines (name, ip, hostname, environment) VALUES (%s, %s, %s, %s) RETURNING id", 
                                (name, ip, hostname, env)
                            )
                            machine_id = cur.fetchone()[0]
                                
                    # 2. Poll Node Agent (/lab-health)
                    try:
                        svc_query = ",".join(projects)
                        url = f"http://{ip}:8000/lab-health?services={svc_query}"
                        response = requests.get(url, timeout=5)
                        if response.status_code == 200:
                            payload = response.json()
                            load = payload.get("system_load", [0, 0, 0])
                            cpu_temp = payload.get("cpu_temp")
                            mem_total = payload.get("mem_total", 0)
                            mem_used = payload.get("mem_used", 0)
                            mem_percent = payload.get("mem_percent", 0.0)
                            disk_read_bytes = payload.get("disk_read_bytes", 0)
                            disk_write_bytes = payload.get("disk_write_bytes", 0)
                            net_sent_bytes = payload.get("net_sent_bytes", 0)
                            net_recv_bytes = payload.get("net_recv_bytes", 0)
                            ssd_ok = payload.get("ssd_ok", True)
                            branch = payload.get("branch", "unknown")
                            
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO hw_metrics (machine_id, cpu_load_1m, cpu_load_5m, cpu_load_15m, 
                                    cpu_temp_c, mem_total, mem_used, mem_percent, disk_read_bytes, disk_write_bytes, 
                                    net_sent_bytes, net_recv_bytes, ssd_ok, lab_health_branch)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                """, (machine_id, load[0], load[1], load[2], cpu_temp, mem_total, mem_used, mem_percent, 
                                      disk_read_bytes, disk_write_bytes, net_sent_bytes, net_recv_bytes, ssd_ok, branch))
                            print(f"[{name}] Logged HW metrics.")

                            # Log systemd services
                            for sys_svc in payload.get("systemd_services", []):
                                svc_name = sys_svc.get("name")
                                is_healthy = sys_svc.get("is_healthy", False)
                                status_str = sys_svc.get("status", "unknown")
                                with conn.cursor() as cur:
                                    cur.execute("INSERT INTO system_services (machine_id, name, type) VALUES (%s, %s, %s) ON CONFLICT ON CONSTRAINT unique_service_per_machine DO UPDATE SET type='systemd' RETURNING id", (machine_id, svc_name, "systemd"))
                                    sys_svc_id = cur.fetchone()[0]
                                    cur.execute("INSERT INTO service_health (service_id, status, is_healthy) VALUES (%s, %s, %s)", (sys_svc_id, status_str, is_healthy))

                            # Log docker containers
                            for container in payload.get("docker_containers", []):
                                c_name = container.get("name")
                                is_healthy = container.get("is_healthy", False)
                                c_status = container.get("status", "unknown")
                                with conn.cursor() as cur:
                                    cur.execute("INSERT INTO system_services (machine_id, name, type) VALUES (%s, %s, %s) ON CONFLICT ON CONSTRAINT unique_service_per_machine DO UPDATE SET type='docker' RETURNING id", (machine_id, c_name, "docker"))
                                    c_svc_id = cur.fetchone()[0]
                                    cur.execute("INSERT INTO service_health (service_id, status, is_healthy) VALUES (%s, %s, %s)", (c_svc_id, c_status, is_healthy))

                        else:
                            print(f"[{name}] Agent status code {response.status_code}")
                    except Exception as e:
                        print(f"[{name}] Could not reach agent at {ip}:8000: {e}")

                    # 3. Check mDNS / Hostname Resolution
                    if hostname:
                        dns_res = check_hostname_resolution(hostname, ip)
                        with conn.cursor() as cur:
                            cur.execute("INSERT INTO external_checks (name, target, check_type) VALUES (%s, %s, %s) ON CONFLICT (name) DO UPDATE SET target=%s RETURNING id", (f"{name}-dns", hostname, "dns", hostname))
                            check_id = cur.fetchone()[0]
                            cur.execute("INSERT INTO external_check_logs (check_id, is_healthy, details) VALUES (%s, %s, %s)", (check_id, dns_res["is_match"], json.dumps(dns_res)))

                    # 4. External Web & API Probes
                    for ext_svc in machine.get("external_services", []):
                        svc_name = ext_svc.get("name")
                        svc_url = ext_svc.get("url")
                        check_ssl = ext_svc.get("check_ssl", False)
                        check_cors = ext_svc.get("check_cors", False)

                        # HTTP Probe
                        start_t = time.time()
                        try:
                            resp = requests.get(svc_url, timeout=5)
                            r_time = int((time.time() - start_t) * 1000)
                            is_healthy = resp.status_code == 200
                            status_code = resp.status_code
                        except Exception as e:
                            r_time = int((time.time() - start_t) * 1000)
                            is_healthy = False
                            status_code = 0

                        with conn.cursor() as cur:
                            cur.execute("INSERT INTO external_checks (name, target, check_type) VALUES (%s, %s, %s) ON CONFLICT (name) DO UPDATE SET target=%s RETURNING id", (svc_name, svc_url, "http", svc_url))
                            ext_id = cur.fetchone()[0]
                            cur.execute("INSERT INTO external_check_logs (check_id, status_code, response_time_ms, is_healthy) VALUES (%s, %s, %s, %s)", (ext_id, status_code, r_time, is_healthy))

                        # SSL Cert Expiry Check
                        if check_ssl and svc_url.startswith("https://"):
                            domain_part = svc_url.replace("https://", "").split("/")[0]
                            port = 443
                            if ":" in domain_part:
                                domain_part, port_str = domain_part.split(":")
                                port = int(port_str)
                            days_rem = get_ssl_expiry_days(domain_part, port=port)
                            with conn.cursor() as cur:
                                cur.execute("INSERT INTO external_checks (name, target, check_type) VALUES (%s, %s, %s) ON CONFLICT (name) DO UPDATE SET target=%s RETURNING id", (f"{svc_name}-ssl", domain_part, "ssl_cert", domain_part))
                                ssl_check_id = cur.fetchone()[0]
                                cur.execute("INSERT INTO external_check_logs (check_id, is_healthy, ssl_days_remaining, details) VALUES (%s, %s, %s, %s)", (ssl_check_id, days_rem > 7, days_rem, json.dumps({"days_remaining": days_rem})))

                        # CORS Preflight Check
                        if check_cors:
                            cors_res = check_cors_preflight(svc_url)
                            with conn.cursor() as cur:
                                cur.execute("INSERT INTO external_checks (name, target, check_type) VALUES (%s, %s, %s) ON CONFLICT (name) DO UPDATE SET target=%s RETURNING id", (f"{svc_name}-cors", svc_url, "cors", svc_url))
                                cors_check_id = cur.fetchone()[0]
                                cur.execute("INSERT INTO external_check_logs (check_id, status_code, response_time_ms, is_healthy, details) VALUES (%s, %s, %s, %s, %s)", (cors_check_id, cors_res["status_code"], cors_res["response_time_ms"], cors_res["is_cors_valid"], json.dumps(cors_res)))

                # 5. Monitor Postgres on Icehouse
                monitor_icehouse_postgres_queries(conn)

        except Exception as e:
            print(f"Database/Monitor loop error: {e}")
            
        print(f"Sleeping for {poll_interval} seconds...")
        time.sleep(poll_interval)

if __name__ == "__main__":
    monitor_loop()
