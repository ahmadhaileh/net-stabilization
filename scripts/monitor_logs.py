#!/usr/bin/env python3
"""
Real-time log monitor for miners and application.

Shows timestamped logs from:
- Both miners (cgminer kernel logs)
- Application logs (nohup.out)

Usage:
    python3 scripts/monitor_logs.py
"""

import threading
import time
import requests
from requests.auth import HTTPDigestAuth
from datetime import datetime
import sys
import os

# Miner config
MINERS = [
    {"ip": "192.168.1.56", "name": "M56"},
    {"ip": "192.168.1.167", "name": "M167"},
]
USERNAME = "root"
PASSWORD = "root"

# Colors for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

# Lock for thread-safe printing
print_lock = threading.Lock()

def timestamp():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]

def safe_print(msg):
    """Thread-safe print"""
    with print_lock:
        print(msg, flush=True)

def print_app(msg):
    """Print app log line"""
    safe_print(f"{Colors.GREEN}[{timestamp()}] APP: {msg}{Colors.ENDC}")

def print_miner(name, msg, color=Colors.CYAN):
    """Print miner log line"""
    safe_print(f"{color}[{timestamp()}] {name}: {msg}{Colors.ENDC}")

def should_show_line(line):
    """Filter which miner log lines to show"""
    # Skip these noisy patterns
    skip_patterns = [
        "Asic[", "get RT hashrate", "Check Chain", "ASIC RT error",
        "asic index", "Done check", "do read temp", "Done read temp",
        "CRC error counter"
    ]
    
    line_lower = line.lower()
    
    for pattern in skip_patterns:
        if pattern.lower() in line_lower:
            return False
    
    # Always show these keywords
    keywords = [
        "freq", "voltage", "start", "stop", "config", "pool",
        "restart", "error", "fail", "reboot", "init", "cgminer",
        "chain[", "pwm", "fan", "temp"
    ]
    
    for kw in keywords:
        if kw.lower() in line_lower:
            return True
    
    return False

def tail_app_logs():
    """Tail the application log file"""
    log_file = "nohup.out"
    
    if not os.path.exists(log_file):
        print_app("nohup.out not found, waiting...")
        while not os.path.exists(log_file):
            time.sleep(1)
    
    # Start from end of file
    with open(log_file, 'r') as f:
        f.seek(0, 2)  # Go to end
        while True:
            line = f.readline()
            if line:
                line = line.strip()
                # Skip HTTP request logs
                if line and not line.startswith("INFO:") and "HTTP/1.1" not in line:
                    print_app(line)
            else:
                time.sleep(0.1)

def fetch_miner_log(miner, last_lines):
    """Fetch and diff miner kernel log"""
    ip = miner["ip"]
    name = miner["name"]
    url = f"http://{ip}/cgi-bin/get_kernel_log.cgi"
    color = Colors.CYAN if name == "M56" else Colors.YELLOW
    
    try:
        resp = requests.get(url, auth=HTTPDigestAuth(USERNAME, PASSWORD), timeout=5)
        if resp.status_code == 200:
            text = resp.text
            lines = text.strip().split('\n')
            
            # Find new lines
            new_lines = []
            if last_lines.get(ip):
                # Find where old log ends by looking at last 100 lines
                old_set = set(last_lines[ip][-100:])
                for line in lines:
                    if line not in old_set:
                        new_lines.append(line)
            else:
                # First fetch - show last 5 interesting lines
                interesting = [l for l in lines if should_show_line(l)]
                new_lines = interesting[-5:] if len(interesting) > 5 else interesting
            
            last_lines[ip] = lines
            
            # Print interesting new lines
            for line in new_lines:
                line = line.strip()
                if line and should_show_line(line):
                    print_miner(name, line, color)
                    
    except requests.exceptions.Timeout:
        print_miner(name, "TIMEOUT - not responding", Colors.RED)
    except requests.exceptions.ConnectionError:
        print_miner(name, "CONNECTION ERROR - offline?", Colors.RED)
    except Exception as e:
        print_miner(name, f"ERROR: {type(e).__name__}: {e}", Colors.RED)

def poll_miners(interval=3):
    """Poll miner logs periodically"""
    last_lines = {}
    
    while True:
        for miner in MINERS:
            fetch_miner_log(miner, last_lines)
        time.sleep(interval)

def main():
    safe_print(f"{Colors.BOLD}=== Miner & App Log Monitor ==={Colors.ENDC}")
    safe_print(f"{Colors.GREEN}APP logs in green{Colors.ENDC}")
    safe_print(f"{Colors.CYAN}M56 (192.168.1.56) in cyan{Colors.ENDC}")
    safe_print(f"{Colors.YELLOW}M167 (192.168.1.167) in yellow{Colors.ENDC}")
    safe_print(f"{Colors.RED}Errors in red{Colors.ENDC}")
    safe_print("-" * 60)
    
    # Start app log tailer in thread
    app_thread = threading.Thread(target=tail_app_logs, daemon=True)
    app_thread.start()
    
    # Poll miners in main thread
    poll_miners(interval=2)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        safe_print(f"\n{Colors.BOLD}Stopped.{Colors.ENDC}")
        sys.exit(0)
