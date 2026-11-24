#!/usr/bin/env python3
"""
oracle_diag.py – uniwersalne narzędzie diagnostyki bazy Oracle
Obsługuje parametry z CLI, zmiennych środowiskowych i pliku konfiguracyjnego.

Użycie:
  python3 oracle_diag.py [opcje]
  python3 oracle_diag.py --config /etc/oracle_diag.ini
  python3 oracle_diag.py --host 192.168.0.66 --port 1521 --sid SATEST -u zewnuser -p zewnuser

Kolejność priorytetów (malejąco):
  1. Argumenty CLI
  2. Zmienne środowiskowe (ORA_HOST, ORA_PORT, ORA_SID, ORA_USER, ORA_PASS, ORA_CLIENT)
  3. Plik konfiguracyjny (domyślnie: oracle_diag.ini w katalogu skryptu)
  4. Wartości domyślne
"""

import argparse
import configparser
import os
import sys
import time
import socket
import struct
from pathlib import Path

# ── Wartości domyślne ────────────────────────────────────────────────────────
DEFAULTS = {
    "host":           "localhost",
    "port":           "1521",
    "sid":            "",
    "service_name":   "",
    "user":           "",
    "password":       "",
    "instant_client": "",
    "timeout":        "10",
}

SCRIPT_DIR = Path(__file__).parent

# Linux struct tcp_info prefix: 8 x u8, then u32 fields up to tcpi_total_retrans.
TCP_INFO_FORMAT = "8B24I"
TCP_INFO_UNACKED = 8 + 4
TCP_INFO_TOTAL_RETRANS = 8 + 23
URG_ACK_WAIT_SECONDS = 5.0


# ── Ładowanie konfiguracji ───────────────────────────────────────────────────

def load_config(config_path: str) -> dict:
    """Wczytuje plik INI i zwraca słownik z sekcji [oracle]."""
    cfg = configparser.ConfigParser()
    if config_path and Path(config_path).exists():
        cfg.read(config_path)
        if cfg.has_section("oracle"):
            return dict(cfg["oracle"])
    return {}


def resolve_config(args) -> dict:
    """Scala konfigurację wg priorytetu: CLI > ENV > plik > domyślne."""

    # Plik konfiguracyjny
    config_path = args.config or str(SCRIPT_DIR / "oracle_diag.ini")
    file_cfg = load_config(config_path)

    def get(key, env_var):
        # 1. CLI
        cli_val = getattr(args, key, None)
        if cli_val is not None and cli_val != "":
            return str(cli_val)
        # 2. ENV
        env_val = os.environ.get(env_var)
        if env_val:
            return env_val
        # 3. Plik
        if key in file_cfg:
            return file_cfg[key]
        # 4. Domyślne
        return DEFAULTS.get(key, "")

    return {
        "host":           get("host",           "ORA_HOST"),
        "port":           int(get("port",        "ORA_PORT") or 1521),
        "sid":            get("sid",             "ORA_SID"),
        "service_name":   get("service_name",    "ORA_SERVICE"),
        "user":           get("user",            "ORA_USER"),
        "password":       get("password",        "ORA_PASS"),
        "instant_client": get("instant_client",  "ORA_CLIENT"),
        "timeout":        int(get("timeout",     "ORA_TIMEOUT") or 10),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def section(title):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


def run_query(cursor, label, sql, headers, col_widths):
    try:
        cursor.execute(sql)
        rows = cursor.fetchall()
        if not rows:
            print(f"  {label}: brak wyników")
            return 0

        header = "  " + "  ".join(h.ljust(w) for h, w in zip(headers, col_widths))
        print(header)
        print("  " + "  ".join("─" * w for w in col_widths))
        for row in rows:
            line = "  " + "  ".join(
                str(v if v is not None else "NULL").ljust(w)[:w]
                for v, w in zip(row, col_widths)
            )
            print(line)
        print(f"  ({len(rows)} wierszy)")
        return len(rows)
    except Exception as e:
        print(f"  ⚠  Błąd [{label}]: {e}")
        return -1


# ── Testy ────────────────────────────────────────────────────────────────────

def test_tcp(host, port, timeout):
    print(f"\n[TCP] {host}:{port} ...", end=" ", flush=True)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            print("✓ Port otwarty")
            return True
    except Exception as e:
        print(f"✗ {e}")
        return False


def read_tcp_ack_state(sock):
    """Return (unacked_segments, total_retransmissions) from Linux TCP_INFO."""
    size = struct.calcsize(TCP_INFO_FORMAT)
    fields = struct.unpack(TCP_INFO_FORMAT, sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, size))
    return fields[TCP_INFO_UNACKED], fields[TCP_INFO_TOTAL_RETRANS]


def wait_for_ack(sock, wait_seconds):
    """Poll until every sent segment is acknowledged; return (acked, total_retransmissions)."""
    deadline = time.monotonic() + wait_seconds
    while True:
        unacked, retransmissions = read_tcp_ack_state(sock)
        if unacked == 0:
            return True, retransmissions
        if time.monotonic() >= deadline:
            return False, retransmissions
        time.sleep(0.1)


def test_urgent_data(host, port, timeout):
    """
    Check whether a TCP segment with the URG flag reaches the server.

    Oracle thin drivers send one urgent byte during connect (out-of-band break check).
    Some firewalls drop such segments, leaving the session hanging until the server
    resets it (INBOUND_CONNECT_TIMEOUT). The probe sends a plain byte as a control,
    then one urgent byte, and reads kernel ACK state via TCP_INFO (Linux only).
    The listener receives an incomplete TNS packet and may log an error.
    """
    print(f"\n[URG] {host}:{port} ...", end=" ", flush=True)
    if not sys.platform.startswith("linux") or not hasattr(socket, "TCP_INFO"):
        print("skipped (requires Linux TCP_INFO)")
        return None

    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            sock.send(b"\x00")
            control_acked, _ = wait_for_ack(sock, URG_ACK_WAIT_SECONDS)
            if not control_acked:
                print(f"? inconclusive: plain data not acknowledged within {URG_ACK_WAIT_SECONDS:.0f}s")
                return None

            _, retransmissions_before = read_tcp_ack_state(sock)
            sock.send(b"\x00", socket.MSG_OOB)
            urgent_acked, retransmissions_after = wait_for_ack(sock, URG_ACK_WAIT_SECONDS)
    except OSError as e:
        print(f"? inconclusive: {e}")
        return None

    urgent_retransmissions = retransmissions_after - retransmissions_before
    if urgent_acked:
        print(f"✓ Urgent (URG) segment acknowledged (retransmissions: {urgent_retransmissions})")
        return True

    print(f"✗ Urgent (URG) segment NOT acknowledged within {URG_ACK_WAIT_SECONDS:.0f}s "
          f"(retransmissions: {urgent_retransmissions}); plain data was acknowledged")
    print("  A device on the path most likely drops TCP segments with the URG flag.")
    print("  Workarounds: JDBC oracle.net.disableOob=true, python-oracledb disable_oob=True,")
    print("  server sqlnet.ora DISABLE_OOB=ON. Root fix: allow the URG flag on port 1521.")
    return False


def connect_oracle(cfg, disable_oob=False):
    try:
        import oracledb
    except ImportError:
        print("✗ Brak modułu oracledb. Zainstaluj: pip install oracledb")
        sys.exit(1)

    # Thick mode jeśli podano instant_client
    if cfg["instant_client"]:
        print(f"[ORA] Tryb thick, Instant Client: {cfg['instant_client']} ...", end=" ", flush=True)
        try:
            oracledb.init_oracle_client(lib_dir=cfg["instant_client"])
            print("✓")
        except Exception as e:
            print(f"✗ {e}")
            sys.exit(1)
    else:
        print("[ORA] Tryb thin (bez Instant Client)")

    # DSN: SID lub service_name
    if cfg["sid"]:
        dsn = f"{cfg['host']}:{cfg['port']}/{cfg['sid']}"
    elif cfg["service_name"]:
        dsn = f"{cfg['host']}:{cfg['port']}/{cfg['service_name']}"
    else:
        print("✗ Podaj --sid lub --service-name")
        sys.exit(1)

    connect_options = {}
    if disable_oob:
        if cfg["instant_client"]:
            print("[ORA] --disable-oob applies to thin mode only; set DISABLE_OOB=ON in client sqlnet.ora for thick mode")
        else:
            print("[ORA] Out-of-band break check disabled (disable_oob=True)")
            connect_options["disable_oob"] = True

    print(f"[ORA] Łączenie z {dsn} jako {cfg['user']} ...", end=" ", flush=True)
    try:
        start = time.time()
        conn = oracledb.connect(user=cfg["user"], password=cfg["password"], dsn=dsn, **connect_options)
        elapsed = time.time() - start
        print(f"✓ OK ({elapsed:.2f}s)")
        return conn, dsn
    except Exception as e:
        print(f"✗ {e}")
        sys.exit(1)


def run_diagnostics(conn, user):
    cur = conn.cursor()

    section("1. INFORMACJE O BAZIE")
    run_query(cur, "Wersja", "SELECT banner FROM v$version",
              ["Banner"], [72])
    run_query(cur, "Czas serwera",
              "SELECT TO_CHAR(SYSDATE,'YYYY-MM-DD HH24:MI:SS') FROM dual",
              ["Czas serwera"], [20])

    section("2. BIEŻĄCA SESJA")
    run_query(cur, "Sesja",
              """SELECT SYS_CONTEXT('USERENV','SESSION_USER') AS uzytkownik,
                        SYS_CONTEXT('USERENV','DB_NAME')      AS baza,
                        SYS_CONTEXT('USERENV','IP_ADDRESS')   AS ip_klienta,
                        SYS_CONTEXT('USERENV','OS_USER')      AS os_user
                 FROM dual""",
              ["Użytkownik", "Baza", "IP klienta", "OS User"], [16, 14, 18, 14])

    section("3. TABELE WŁASNE UŻYTKOWNIKA (USER_TABLES)")
    run_query(cur, "Tabele",
              "SELECT table_name, num_rows, last_analyzed FROM user_tables ORDER BY table_name",
              ["Tabela", "Wiersze", "Ostatnia analiza"], [35, 10, 20])

    section(f"4. GRANTY NA OBIEKTY SCHEMATU (ALL_TAB_PRIVS dla {user.upper()})")
    run_query(cur, "Granty",
              f"""SELECT table_schema, table_name, privilege, grantable
                  FROM all_tab_privs
                  WHERE grantee = UPPER('{user}')
                  ORDER BY table_schema, table_name, privilege""",
              ["Schema", "Obiekt", "Przywilej", "Grantable"], [18, 36, 16, 10])

    section("5. UPRAWNIENIA SYSTEMOWE")
    run_query(cur, "Przywileje",
              "SELECT privilege FROM user_sys_privs ORDER BY privilege",
              ["Przywilej"], [40])

    section("6. ROLE")
    run_query(cur, "Role",
              "SELECT granted_role, admin_option FROM user_role_privs ORDER BY granted_role",
              ["Rola", "Admin"], [32, 6])

    section("7. TABLESPACE")
    run_query(cur, "Tablespace",
              """SELECT tablespace_name,
                        ROUND(bytes/1024/1024,1)     AS mb_przydzielone,
                        ROUND(max_bytes/1024/1024,1) AS mb_max
                 FROM user_ts_quotas ORDER BY tablespace_name""",
              ["Tablespace", "MB przydzielone", "MB max"], [22, 16, 10])

    section("8. AKTYWNE SESJE (v$session)")
    run_query(cur, "Sesje",
              """SELECT username, status, COUNT(*) AS ile
                 FROM v$session WHERE username IS NOT NULL
                 GROUP BY username, status ORDER BY username""",
              ["Użytkownik", "Status", "Ile"], [22, 10, 5])

    cur.close()


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(
        description="Narzędzie diagnostyki bazy Oracle",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Przykłady:
  # Parametry z CLI:
  python3 oracle_diag.py --host 192.168.0.66 --sid SATEST -u zewnuser -p zewnuser

  # Thick mode (Oracle 11g):
  python3 oracle_diag.py --host 192.168.0.66 --sid SATEST -u zewnuser -p zewnuser \\
    --instant-client /root/oracle/instantclient_21_15

  # Z pliku konfiguracyjnego:
  python3 oracle_diag.py --config /etc/oracle_diag.ini

  # Ze zmiennych środowiskowych:
  export ORA_HOST=192.168.0.66 ORA_SID=SATEST ORA_USER=zewnuser ORA_PASS=zewnuser
  python3 oracle_diag.py
        """,
    )

    conn = p.add_argument_group("Połączenie")
    conn.add_argument("--host",           metavar="IP/HOST",   help="Adres serwera Oracle")
    conn.add_argument("--port",           metavar="PORT",      type=int, help="Port (domyślnie: 1521)")
    conn.add_argument("--sid",            metavar="SID",       help="SID bazy danych")
    conn.add_argument("--service-name",   metavar="SERVICE",   dest="service_name", help="Service name (alternatywa dla SID)")
    conn.add_argument("-u", "--user",     metavar="USER",      help="Nazwa użytkownika")
    conn.add_argument("-p", "--password", metavar="PASS",      help="Hasło")
    conn.add_argument("--instant-client", metavar="PATH",      dest="instant_client",
                      help="Ścieżka do Oracle Instant Client (wymagane dla Oracle ≤ 12c lub thick mode)")
    conn.add_argument("--timeout",        metavar="SECS",      type=int, help="Timeout TCP (domyślnie: 10)")

    other = p.add_argument_group("Inne")
    other.add_argument("--config",        metavar="FILE",      help="Plik konfiguracyjny INI (domyślnie: oracle_diag.ini)")
    other.add_argument("--no-diag",       action="store_true", help="Tylko test połączenia, bez pełnej diagnostyki")
    other.add_argument("--urg-test",      action="store_true", dest="urg_test",
                       help="Check whether TCP segments with the URG flag reach the server (Linux only)")
    other.add_argument("--disable-oob",   action="store_true", dest="disable_oob",
                       help="Connect without the out-of-band break check (thin mode only)")
    other.add_argument("--generate-config", action="store_true", help="Wygeneruj przykładowy plik oracle_diag.ini i zakończ")

    return p


def generate_config():
    content = """\
[oracle]
host           = 192.168.0.66
port           = 1521
sid            = SATEST
; service_name = SATEST_SERVICE   ; alternatywa dla SID
user           = zewnuser
password       = zewnuser
instant_client = /root/oracle/instantclient_21_15
timeout        = 10
"""
    out = Path("oracle_diag.ini")
    out.write_text(content)
    print(f"✓ Wygenerowano: {out.resolve()}")
    print("  Uzupełnij dane i uruchom: python3 oracle_diag.py --config oracle_diag.ini")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.generate_config:
        generate_config()
        sys.exit(0)

    cfg = resolve_config(args)

    # Walidacja
    missing = [k for k in ("host", "user", "password") if not cfg[k]]
    if missing or (not cfg["sid"] and not cfg["service_name"]):
        missing_str = ", ".join(missing)
        if not cfg["sid"] and not cfg["service_name"]:
            missing_str += (", " if missing_str else "") + "sid/service_name"
        print(f"✗ Brakuje wymaganych parametrów: {missing_str}")
        print("  Użyj --help aby zobaczyć opcje.")
        sys.exit(1)

    dsn_str = f"{cfg['host']}:{cfg['port']}/{cfg['sid'] or cfg['service_name']}"
    print("=" * 60)
    print("  DIAGNOSTYKA BAZY ORACLE")
    print(f"  {dsn_str}  →  user: {cfg['user']}")
    print("=" * 60)

    if not test_tcp(cfg["host"], cfg["port"], cfg["timeout"]):
        print("\n⚠  Port niedostępny – sprawdź firewall / adres IP.")
        sys.exit(1)

    if args.urg_test:
        urgent_ok = test_urgent_data(cfg["host"], cfg["port"], cfg["timeout"])
        if urgent_ok is False and not args.disable_oob:
            print("  ⚠  The connect below may hang until the server resets it; consider --disable-oob.")

    conn, dsn = connect_oracle(cfg, disable_oob=args.disable_oob)

    if not args.no_diag:
        run_diagnostics(conn, cfg["user"])

    conn.close()

    print(f"\n{'=' * 60}")
    print("  ✅ Diagnostyka zakończona pomyślnie.")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
