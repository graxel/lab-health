import os
import psycopg
from dotenv import load_dotenv

def setup_db():
    load_dotenv("settings.env")
    load_dotenv("secrets.env")
    
    conn_info = f"""
        host={os.getenv("DB_HOST", "localhost")} 
        port={os.getenv("DB_PORT", "5432")} 
        dbname={os.getenv("DB_NAME", "lab_health")} 
        user={os.getenv("DB_USER", "postgres")} 
        password={os.getenv("DB_PASSWORD", "")}
    """
    
    try:
        with psycopg.connect(conn_info, autocommit=True) as conn:
            with conn.cursor() as cur:
                # Drop existing tables cleanly
                cur.execute("DROP TABLE IF EXISTS postgres_heavy_queries CASCADE;")
                cur.execute("DROP TABLE IF EXISTS external_check_logs CASCADE;")
                cur.execute("DROP TABLE IF EXISTS external_checks CASCADE;")
                cur.execute("DROP TABLE IF EXISTS service_health CASCADE;")
                cur.execute("DROP TABLE IF EXISTS system_services CASCADE;")
                cur.execute("DROP TABLE IF EXISTS services CASCADE;")
                cur.execute("DROP TABLE IF EXISTS hw_metrics CASCADE;")
                cur.execute("DROP TABLE IF EXISTS machines CASCADE;")

                # Create machines table
                cur.execute("""
                    CREATE TABLE machines (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) UNIQUE NOT NULL,
                        ip VARCHAR(255) NOT NULL,
                        hostname VARCHAR(255),
                        environment VARCHAR(50) DEFAULT 'prod'
                    );
                """)
                
                # Create hw_metrics table
                cur.execute("""
                    CREATE TABLE hw_metrics (
                        id BIGSERIAL PRIMARY KEY,
                        timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        machine_id INTEGER REFERENCES machines(id) ON DELETE CASCADE,
                        cpu_load_1m FLOAT,
                        cpu_load_5m FLOAT,
                        cpu_load_15m FLOAT,
                        cpu_temp_c FLOAT,
                        mem_total BIGINT,
                        mem_used BIGINT,
                        mem_percent FLOAT,
                        disk_read_bytes BIGINT,
                        disk_write_bytes BIGINT,
                        net_sent_bytes BIGINT,
                        net_recv_bytes BIGINT,
                        ssd_ok BOOLEAN DEFAULT TRUE,
                        lab_health_branch VARCHAR(255)
                    );
                """)

                # Create system_services table (Docker & Systemd)
                cur.execute("""
                    CREATE TABLE system_services (
                        id SERIAL PRIMARY KEY,
                        machine_id INTEGER REFERENCES machines(id) ON DELETE CASCADE,
                        name VARCHAR(255) NOT NULL,
                        type VARCHAR(50) NOT NULL,
                        CONSTRAINT unique_service_per_machine UNIQUE (machine_id, name, type)
                    );
                """)
                
                # Create service_health table
                cur.execute("""
                    CREATE TABLE service_health (
                        id BIGSERIAL PRIMARY KEY,
                        timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        service_id INTEGER REFERENCES system_services(id) ON DELETE CASCADE,
                        status VARCHAR(50) NOT NULL,
                        is_healthy BOOLEAN NOT NULL,
                        error_message TEXT
                    );
                """)
                
                # Create external_checks table
                cur.execute("""
                    CREATE TABLE external_checks (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(255) UNIQUE NOT NULL,
                        target VARCHAR(255) NOT NULL,
                        check_type VARCHAR(50) NOT NULL
                    );
                """)

                # Create external_check_logs table
                cur.execute("""
                    CREATE TABLE external_check_logs (
                        id BIGSERIAL PRIMARY KEY,
                        timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        check_id INTEGER REFERENCES external_checks(id) ON DELETE CASCADE,
                        status_code INTEGER,
                        response_time_ms INTEGER,
                        is_healthy BOOLEAN NOT NULL,
                        ssl_days_remaining INTEGER,
                        details JSONB
                    );
                """)

                # Create postgres_heavy_queries table
                cur.execute("""
                    CREATE TABLE postgres_heavy_queries (
                        id BIGSERIAL PRIMARY KEY,
                        timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        query_id BIGINT,
                        datname VARCHAR(255),
                        calls BIGINT,
                        total_exec_time_ms DOUBLE PRECISION,
                        mean_exec_time_ms DOUBLE PRECISION,
                        query_text TEXT
                    );
                """)

                # Create performance indices
                cur.execute("CREATE INDEX idx_hw_metrics_machine_time ON hw_metrics (machine_id, timestamp DESC);")
                cur.execute("CREATE INDEX idx_service_health_service_time ON service_health (service_id, timestamp DESC);")
                cur.execute("CREATE INDEX idx_ext_check_logs_check_time ON external_check_logs (check_id, timestamp DESC);")
                cur.execute("CREATE INDEX idx_heavy_queries_time ON postgres_heavy_queries (timestamp DESC);")
                
                print("Database tables & indexes created successfully.")
    except Exception as e:
        print(f"Error creating tables: {e}")

if __name__ == "__main__":
    setup_db()
