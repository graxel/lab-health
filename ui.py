import os
import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
import uvicorn

app = FastAPI(title="Lab Health Dashboard")

load_dotenv("settings.env")
load_dotenv("secrets.env")

conn_info = f"""
    host={os.getenv("DB_HOST", "localhost")} 
    port={os.getenv("DB_PORT", "5432")} 
    dbname={os.getenv("DB_NAME", "lab_health")} 
    user={os.getenv("DB_USER", "postgres")} 
    password={os.getenv("DB_PASSWORD", "")}
"""

def fetch_data():
    hw_rows, svc_rows, ext_rows, query_rows = [], [], [], []
    try:
        with psycopg.connect(conn_info) as conn:
            with conn.cursor() as cur:
                # Hardware Metrics
                cur.execute("""
                    WITH RankedMetrics AS (
                        SELECT m.name, m.ip, m.hostname, h.timestamp, h.cpu_load_1m, h.cpu_temp_c, 
                               h.mem_percent, h.ssd_ok, h.lab_health_branch,
                               ROW_NUMBER() OVER (PARTITION BY m.name ORDER BY h.timestamp DESC) as rn
                        FROM hw_metrics h
                        JOIN machines m ON h.machine_id = m.id
                    )
                    SELECT name, ip, hostname, timestamp, cpu_load_1m, cpu_temp_c, mem_percent, ssd_ok, lab_health_branch
                    FROM RankedMetrics WHERE rn = 1 ORDER BY name;
                """)
                hw_rows = cur.fetchall()

                # Services
                cur.execute("""
                    WITH RankedServices AS (
                        SELECT s.name as service_name, s.type, m.name as machine_name, 
                               sh.timestamp, sh.status, sh.is_healthy,
                               ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY sh.timestamp DESC) as rn
                        FROM service_health sh
                        JOIN system_services s ON sh.service_id = s.id
                        JOIN machines m ON s.machine_id = m.id
                    )
                    SELECT service_name, type, machine_name, status, is_healthy, timestamp
                    FROM RankedServices WHERE rn = 1 ORDER BY machine_name, service_name;
                """)
                svc_rows = cur.fetchall()

                # External Checks
                cur.execute("""
                    WITH RankedExt AS (
                        SELECT ec.name, ec.target, ec.check_type, ecl.status_code, ecl.response_time_ms, ecl.is_healthy, ecl.ssl_days_remaining, ecl.timestamp,
                               ROW_NUMBER() OVER (PARTITION BY ec.id ORDER BY ecl.timestamp DESC) as rn
                        FROM external_check_logs ecl
                        JOIN external_checks ec ON ecl.check_id = ec.id
                    )
                    SELECT name, check_type, target, status_code, response_time_ms, ssl_days_remaining, is_healthy, timestamp
                    FROM RankedExt WHERE rn = 1 ORDER BY check_type, name;
                """)
                ext_rows = cur.fetchall()

                # Heavy Queries
                cur.execute("""
                    SELECT timestamp, datname, calls, ROUND(total_exec_time_ms::numeric, 1), ROUND(mean_exec_time_ms::numeric, 1), query_text
                    FROM postgres_heavy_queries ORDER BY timestamp DESC LIMIT 5;
                """)
                query_rows = cur.fetchall()
    except Exception as e:
        print(f"[UI] DB Query Error: {e}")
    return hw_rows, svc_rows, ext_rows, query_rows

@app.get("/", response_class=HTMLResponse)
def index():
    hw_rows, svc_rows, ext_rows, query_rows = fetch_data()

    # Build HTML tables cleanly
    hw_html = ""
    for r in hw_rows:
        temp = f"{r[5]}°C" if r[5] is not None else "N/A"
        ssd = "✅ OK" if r[7] else "❌ Error"
        hw_html += f"""
        <tr class="border-b border-gray-700 hover:bg-gray-800">
            <td class="px-4 py-3 font-semibold text-white">{r[0]}</td>
            <td class="px-4 py-3 text-gray-300">{r[1]}</td>
            <td class="px-4 py-3 text-gray-400 font-mono text-sm">{r[2] or 'N/A'}</td>
            <td class="px-4 py-3 text-gray-300">{r[4]}</td>
            <td class="px-4 py-3 text-yellow-400 font-mono">{temp}</td>
            <td class="px-4 py-3 text-blue-400 font-mono">{round(r[6] or 0, 1)}%</td>
            <td class="px-4 py-3">{ssd}</td>
            <td class="px-4 py-3 text-gray-400 text-xs font-mono">{r[8]}</td>
        </tr>
        """

    svc_html = ""
    for r in svc_rows:
        badge = '<span class="px-2 py-1 bg-green-900 text-green-300 rounded text-xs font-bold">HEALTHY</span>' if r[4] else '<span class="px-2 py-1 bg-red-900 text-red-300 rounded text-xs font-bold">FAILED</span>'
        svc_html += f"""
        <tr class="border-b border-gray-700 hover:bg-gray-800">
            <td class="px-4 py-3 font-semibold text-white">{r[0]}</td>
            <td class="px-4 py-3 text-gray-400 text-xs uppercase">{r[1]}</td>
            <td class="px-4 py-3 text-gray-300">{r[2]}</td>
            <td class="px-4 py-3 font-mono text-sm text-gray-300">{r[3]}</td>
            <td class="px-4 py-3">{badge}</td>
        </tr>
        """

    ext_html = ""
    for r in ext_rows:
        badge = '<span class="px-2 py-1 bg-green-900 text-green-300 rounded text-xs font-bold">PASS</span>' if r[6] else '<span class="px-2 py-1 bg-red-900 text-red-300 rounded text-xs font-bold">FAIL</span>'
        ssl_val = f"{r[5]} days" if r[5] is not None and r[5] >= 0 else "N/A"
        ext_html += f"""
        <tr class="border-b border-gray-700 hover:bg-gray-800">
            <td class="px-4 py-3 font-semibold text-white">{r[0]}</td>
            <td class="px-4 py-3 text-gray-400 text-xs uppercase">{r[1]}</td>
            <td class="px-4 py-3 text-gray-300 text-sm font-mono truncate max-w-xs">{r[2]}</td>
            <td class="px-4 py-3 font-mono text-sm">{r[3] or '-'}</td>
            <td class="px-4 py-3 font-mono text-sm">{r[4]} ms</td>
            <td class="px-4 py-3 font-mono text-sm text-purple-400">{ssl_val}</td>
            <td class="px-4 py-3">{badge}</td>
        </tr>
        """

    query_html = ""
    for r in query_rows:
        query_html += f"""
        <tr class="border-b border-gray-700 hover:bg-gray-800">
            <td class="px-4 py-3 text-gray-300">{r[1]}</td>
            <td class="px-4 py-3 text-gray-300 font-mono">{r[2]}</td>
            <td class="px-4 py-3 text-yellow-400 font-mono">{r[3]} ms</td>
            <td class="px-4 py-3 text-gray-400 font-mono text-xs max-w-md truncate">{r[5]}</td>
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Lab Health Dashboard</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <meta http-equiv="refresh" content="30">
    </head>
    <body class="bg-gray-900 text-gray-100 min-h-screen p-6 font-sans">
        <div class="max-w-7xl mx-auto space-y-8">
            <!-- Header -->
            <div class="flex items-center justify-between border-b border-gray-800 pb-4">
                <div>
                    <h1 class="text-3xl font-bold text-white flex items-center gap-2">🖥️ Lab Health Dashboard</h1>
                    <p class="text-gray-400 text-sm mt-1">Lightweight Monitoring System (sumtingwong • Raspberry Pi 2B)</p>
                </div>
                <span class="text-xs text-gray-500 font-mono">Auto-refreshes every 30s</span>
            </div>

            <!-- Hardware Section -->
            <div class="bg-gray-800 rounded-lg shadow-lg p-6 border border-gray-700">
                <h2 class="text-xl font-semibold text-white mb-4 flex items-center gap-2">📊 Host Hardware & Thermals</h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="border-b border-gray-700 text-gray-400 text-xs uppercase">
                                <th class="px-4 py-2">Machine</th>
                                <th class="px-4 py-2">IP</th>
                                <th class="px-4 py-2">mDNS</th>
                                <th class="px-4 py-2">CPU 1m</th>
                                <th class="px-4 py-2">Temp</th>
                                <th class="px-4 py-2">RAM %</th>
                                <th class="px-4 py-2">SSD</th>
                                <th class="px-4 py-2">Branch</th>
                            </tr>
                        </thead>
                        <tbody>{hw_html or '<tr><td colspan="8" class="p-4 text-center text-gray-500">No hardware data logged yet.</td></tr>'}</tbody>
                    </table>
                </div>
            </div>

            <!-- Systemd Services & Containers -->
            <div class="bg-gray-800 rounded-lg shadow-lg p-6 border border-gray-700">
                <h2 class="text-xl font-semibold text-white mb-4 flex items-center gap-2">🐳 Systemd Services & Docker Containers</h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="border-b border-gray-700 text-gray-400 text-xs uppercase">
                                <th class="px-4 py-2">Service / Container</th>
                                <th class="px-4 py-2">Type</th>
                                <th class="px-4 py-2">Host</th>
                                <th class="px-4 py-2">Status</th>
                                <th class="px-4 py-2">Health</th>
                            </tr>
                        </thead>
                        <tbody>{svc_html or '<tr><td colspan="5" class="p-4 text-center text-gray-500">No service data logged yet.</td></tr>'}</tbody>
                    </table>
                </div>
            </div>

            <!-- External Checks -->
            <div class="bg-gray-800 rounded-lg shadow-lg p-6 border border-gray-700">
                <h2 class="text-xl font-semibold text-white mb-4 flex items-center gap-2">🌐 External Endpoints, SSL & mDNS Resolution</h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="border-b border-gray-700 text-gray-400 text-xs uppercase">
                                <th class="px-4 py-2">Check Name</th>
                                <th class="px-4 py-2">Type</th>
                                <th class="px-4 py-2">Target</th>
                                <th class="px-4 py-2">HTTP</th>
                                <th class="px-4 py-2">Latency</th>
                                <th class="px-4 py-2">SSL Days</th>
                                <th class="px-4 py-2">Status</th>
                            </tr>
                        </thead>
                        <tbody>{ext_html or '<tr><td colspan="7" class="p-4 text-center text-gray-500">No external checks logged yet.</td></tr>'}</tbody>
                    </table>
                </div>
            </div>

            <!-- Heavy Queries -->
            <div class="bg-gray-800 rounded-lg shadow-lg p-6 border border-gray-700">
                <h2 class="text-xl font-semibold text-white mb-4 flex items-center gap-2">🐘 Icehouse Postgres Top Heavy Queries</h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="border-b border-gray-700 text-gray-400 text-xs uppercase">
                                <th class="px-4 py-2">Database</th>
                                <th class="px-4 py-2">Calls</th>
                                <th class="px-4 py-2">Total Time</th>
                                <th class="px-4 py-2">Query</th>
                            </tr>
                        </thead>
                        <tbody>{query_html or '<tr><td colspan="4" class="p-4 text-center text-gray-500">No heavy query logs recorded yet.</td></tr>'}</tbody>
                    </table>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    uvicorn.run("ui:app", host="0.0.0.0", port=8000, reload=True)
