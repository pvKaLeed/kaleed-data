import csv
import io
import json
import re
import socket
import ssl
import base64
import urllib.request
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed


VPN_GATE_URL = "https://www.vpngate.net/api/iphone/"
OUTPUT_FILE = "servers.json"

# Server active test
CONNECT_TIMEOUT = 4

# Maximum acceptable latency
MAX_PING = 3000

# Minimum speed reported by VPN Gate
MIN_SPEED = 1

# Test many servers in parallel
MAX_WORKERS = 30


def download_vpngate():

    print("Downloading VPN Gate server list...")

    request = urllib.request.Request(
        VPN_GATE_URL,
        headers={
            "User-Agent": "KVPN-Server-Updater/1.0"
        }
    )

    context = ssl.create_default_context()

    with urllib.request.urlopen(
        request,
        timeout=60,
        context=context
    ) as response:

        return response.read().decode(
            "utf-8",
            errors="ignore"
        )


def decode_config(value):

    if not value:
        return ""

    try:

        decoded = base64.b64decode(
            value,
            validate=False
        )

        return decoded.decode(
            "utf-8",
            errors="ignore"
        ).strip()

    except Exception:

        return ""


def tcp_ping(host, port):

    start = datetime.now().timestamp()

    try:

        with socket.create_connection(
            (host, port),
            timeout=CONNECT_TIMEOUT
        ):

            end = datetime.now().timestamp()

            return int(
                (end - start) * 1000
            )

    except Exception:

        return None


def parse_vpngate(raw):

    rows = []

    for line in raw.splitlines():

        # VPN Gate CSV comments start with *
        if line.startswith("*"):
            continue

        rows.append(line)

    csv_text = "\n".join(rows)

    reader = csv.DictReader(
        io.StringIO(csv_text)
    )

    servers = []

    for row in reader:

        try:

            ip = (
                row.get("IP", "")
                or row.get("IP Address", "")
            ).strip()

            if not ip:
                continue

            country_code = (
                row.get("CountryShort", "")
                or row.get("CountryCode", "")
                or "XX"
            ).strip().upper()

            country_name = (
                row.get("CountryLong", "")
                or row.get("Country", "")
                or country_code
            ).strip()

            hostname = (
                row.get("HostName", "")
                or ip
            ).strip()

            try:
                uptime = int(
                    row.get("Uptime", "0") or 0
                )
            except:
                uptime = 0

            try:
                speed = int(
                    row.get("Speed", "0") or 0
                )
            except:
                speed = 0

            try:
                sessions = int(
                    row.get(
                        "NumVpnSessions",
                        "0"
                    ) or 0
                )
            except:
                sessions = 0

            try:
                score = int(
                    row.get("Score", "0") or 0
                )
            except:
                score = 0

            try:
                tcp_port = int(
                    row.get("TCPPort", "443") or 443
                )
            except:
                tcp_port = 443

            try:
                udp_port = int(
                    row.get("UDPPort", "0") or 0
                )
            except:
                udp_port = 0

            config_base64 = (
                row.get(
                    "OpenVPN_ConfigData_Base64",
                    ""
                ) or ""
            ).strip()

            config_data = decode_config(
                config_base64
            )

            # -----------------------------
            # Basic active-server filtering
            # -----------------------------

            if not ip:
                continue

            if not config_data:
                continue

            if uptime <= 0:
                continue

            if speed < MIN_SPEED:
                continue

            if tcp_port <= 0:
                continue

            servers.append({
                "ip": ip,
                "host": hostname,
                "countryCode": country_code,
                "countryName": country_name,
                "uptime": uptime,
                "speed": speed,
                "sessions": sessions,
                "score": score,
                "tcpPort": tcp_port,
                "udpPort": udp_port,
                "configData": config_data
            })

        except Exception as e:

            print(
                "Skipping invalid row:",
                e
            )

    return servers


def test_server(server):

    ip = server["ip"]
    port = server["tcpPort"]

    ping = tcp_ping(
        ip,
        port
    )

    if ping is None:
        return None

    if ping > MAX_PING:
        return None

    country_code = server["countryCode"]

    server_id = (
        f"{country_code}_"
        f"{ip}_"
        f"{port}"
    )

    server_id = re.sub(
        r"[^A-Za-z0-9_-]",
        "_",
        server_id
    )

    return {
        "id": server_id,

        "countryCode":
            country_code,

        "countryName":
            server["countryName"],

        "flagUrl":
            f"https://flagcdn.com/w40/"
            f"{country_code.lower()}.png",

        "host":
            server["host"],

        "ip":
            ip,

        "ping":
            ping,

        "speed":
            server["speed"],

        "load":
            server["sessions"],

        "score":
            server["score"],

        "uptime":
            server["uptime"],

        "sessions":
            server["sessions"],

        "protocol":
            "openvpn",

        "port":
            port,

        "tcpPort":
            port,

        "udpPort":
            server["udpPort"],

        "configData":
            server["configData"]
    }


def check_active_servers(servers):

    print(
        f"Testing {len(servers)} servers..."
    )

    active = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = [
            executor.submit(
                test_server,
                server
            )
            for server in servers
        ]

        for future in as_completed(futures):

            try:

                result = future.result()

                if result is not None:
                    active.append(result)

            except Exception:
                pass

    return active


def remove_duplicates(servers):

    unique = {}

    for server in servers:

        key = (
            server["ip"],
            server["tcpPort"]
        )

        # If duplicate exists,
        # keep the faster server
        if key not in unique:

            unique[key] = server

        else:

            old = unique[key]

            if server["ping"] < old["ping"]:
                unique[key] = server

    return list(
        unique.values()
    )


def build_countries(servers):

    countries = {}

    for server in servers:

        code = server["countryCode"]

        if code not in countries:

            countries[code] = {
                "countryCode": code,
                "countryName":
                    server["countryName"],
                "flagUrl":
                    server["flagUrl"],
                "serverCount": 0,
                "bestPing": 999999,
                "bestSpeed": 0
            }

        country = countries[code]

        country["serverCount"] += 1

        country["bestPing"] = min(
            country["bestPing"],
            server["ping"]
        )

        country["bestSpeed"] = max(
            country["bestSpeed"],
            server["speed"]
        )

    return sorted(
        countries.values(),
        key=lambda x: x["countryName"]
    )


def main():

    raw = download_vpngate()

    print(
        f"Downloaded {len(raw)} bytes"
    )

    candidates = parse_vpngate(raw)

    print(
        f"Candidates after basic filter: "
        f"{len(candidates)}"
    )

    active_servers = check_active_servers(
        candidates
    )

    print(
        f"Active servers: "
        f"{len(active_servers)}"
    )

    active_servers = remove_duplicates(
        active_servers
    )

    # Fastest / lowest ping first
    active_servers.sort(
        key=lambda x: (
            x["countryName"],
            x["ping"],
            -x["speed"]
        )
    )

    countries = build_countries(
        active_servers
    )

    output = {

        "source": "VPN Gate",

        "updatedAt":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "serverCount":
            len(active_servers),

        "countryCount":
            len(countries),

        "countries":
            countries,

        "servers":
            active_servers
    }

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    print()
    print(
        "================================"
    )
    print(
        f"Active servers : "
        f"{len(active_servers)}"
    )
    print(
        f"Countries      : "
        f"{len(countries)}"
    )
    print(
        f"Output         : "
        f"{OUTPUT_FILE}"
    )
    print(
        "================================"
    )


if __name__ == "__main__":
    main()
