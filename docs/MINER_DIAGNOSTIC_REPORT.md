# Miner Fleet Diagnostic Report
**Date**: 2026-04-15  
**Scanned**: 173 miners on 192.168.95.0/24 (port 80 reachable)  
**Method**: Vnish API endpoints (get_miner_status, get_system_info, get_miner_conf) with all miners woken via 260 kW activation

---

## Summary

| Category | Count | Description |
|---|---|---|
| **Healthy (OK)** | 80 | 3/3 chains, hashing normally |
| **Still Sleeping** | 66 | Failed to wake — cgminer never started |
| **Partial Chain Failure** | 17 | 1 or 2 dead hash boards, still mining on remaining |
| **Unreachable** | 5 | Not Vnish firmware (404) or wrong auth |
| **Missing ASIC Chips** | 2 | Chains running but with missing ASICs |
| **Other Issues** | 2 | Pool connection dead |
| **Zero Hashrate** | 1 | All 3 chains detected but 0 GH/s |
| **TOTAL** | 173 | |

**Effective mining fleet**: 80 OK + 17 partial + 2 missing chips + 2 pool issue + 1 zero HR = **~99 miners producing hashrate**  
**Completely non-functional**: 66 sleeping + 5 unreachable = **71 miners**

---

## UNREACHABLE / INCOMPATIBLE (5)

These respond on HTTP but don't have Vnish CGI endpoints — likely stock Bitmain or different firmware.

| IP | Issue |
|---|---|
| 192.168.95.2 | NOT VNISH FIRMWARE (HTTP 404) |
| 192.168.95.6 | NOT VNISH FIRMWARE (HTTP 404) — this is the fake stratum server |
| 192.168.95.10 | NOT VNISH FIRMWARE (HTTP 404) |
| 192.168.95.130 | AUTH FAILED (wrong password) |
| 192.168.95.131 | NOT VNISH FIRMWARE (HTTP 404) |

---

## STILL SLEEPING — FAILED TO WAKE (66)

These miners accept the wake command but cgminer never starts. The controller board is alive (web server responds) but mining software won't launch. Causes: dead hash boards preventing initialization, corrupted config, or firmware issues.

| IP | Freq | Pool Config | Firmware |
|---|---|---|---|
| 192.168.95.13 | — | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.14 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.16 | 650 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.21 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.24 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.28 | 500 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.29 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.30 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.38 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.39 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.42 | 625 | 192.168.95.6:3333 | Antminer S9 |
| 192.168.95.47 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.48 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.57 | 625 | 192.168.95.6:3333 | Antminer S9 |
| 192.168.95.60 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.61 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.64 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.67 | 625 | 192.168.95.6:3333 | Antminer S9 |
| 192.168.95.71 | 650 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.72 | 650 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.77 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.79 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.80 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.81 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.82 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.83 | 650 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.84 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.85 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.86 | 625 | 192.168.95.6:3333 | Antminer S9 |
| 192.168.95.89 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.90 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.91 | 625 | 172.65.65.63:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.92 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.94 | 625 | stratum.braiins.com:3333 | Antminer S9i |
| 192.168.95.97 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.105 | 625 | stratum.braiins.com:3333 | Antminer S9i |
| 192.168.95.109 | 625 | 192.168.95.6:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.111 | 625 | 192.168.95.6:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.114 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.116 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.143 | 650 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.145 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.149 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.151 | 625 | 192.168.95.6:3333 | Antminer S9 |
| 192.168.95.152 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.154 | 650 | stratum.braiins.com:3333 | Antminer S9i |
| 192.168.95.159 | 650 | 192.168.95.6:3333 | Antminer S9 |
| 192.168.95.160 | 625 | 192.168.95.6:3333 | Antminer S9 |
| 192.168.95.161 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.168 | 625 | stratum.braiins.com:3333 | Antminer S9 |
| 192.168.95.182 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.184 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.189 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.191 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.193 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.198 | 625 | 192.168.95.6:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.209 | 625 | 192.168.95.6:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.211 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.212 | 625 | 192.168.95.6:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.215 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.220 | 625 | 192.168.95.6:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.221 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.222 | 650 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.223 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.237 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |
| 192.168.95.250 | 625 | stratum.braiins.com:3333 | Antminer S9 (vnish 3.9.0) |

---

## PARTIAL CHAIN FAILURE (17)

Mining on reduced capacity — 1 or 2 of 3 hash boards dead.

| IP | GH/s | Chains | Freq | Temp | Dead Chain(s) | Notes |
|---|---|---|---|---|---|---|
| 192.168.95.9 | 8,798 | 2/3 | 625 | 81°C | chain6 | HIGH HW errors: 694/hr |
| 192.168.95.20 | 7,774 | 2/3 | 625 | 52°C | chain8 | |
| 192.168.95.56 | 8,948 | 2/3 | 625 | 81°C | chain8 | |
| 192.168.95.59 | 0 | 2/3 | 625 | 46°C | chain7 | 2 chains detected but 0 GH/s, pool Dead |
| 192.168.95.70 | 8,946 | 2/3 | 625 | 82°C | chain6 | |
| 192.168.95.135 | 4,248 | 1/3 | 550 | 81°C | chain6, chain7 | Only 1 board working |
| 192.168.95.147 | 8,759 | 2/3 | 625 | 80°C | chain7 | |
| 192.168.95.150 | 4,002 | 1/3 | 625 | 67°C | chain6, chain7 | Only 1 board working |
| 192.168.95.157 | 8,673 | 2/3 | 625 | 82°C | chain8 | |
| 192.168.95.177 | 7,907 | 2/3 | 550 | 81°C | chain6 | |
| 192.168.95.190 | 8,614 | 2/3 | 625 | 60°C | chain7 | |
| 192.168.95.194 | 7,874 | 2/3 | 550 | 81°C | chain6 | |
| 192.168.95.207 | 8,486 | 2/3 | — | 80°C | chain6 | |
| 192.168.95.210 | 9,167 | 2/3 | 625 | 82°C | chain8 | |
| 192.168.95.226 | 4,418 | 2/3 | 625 | 81°C | chain7 | Low HR for 2 chains |
| 192.168.95.227 | 9,222 | 2/3 | 650 | 81°C | chain7 | |
| 192.168.95.240 | 8,815 | 2/3 | 625 | 64°C | chain7 | |

---

## MISSING ASIC CHIPS (2)

Running but with fewer ASICs than expected (63 per chain on S9).

| IP | GH/s | Chains | Issue |
|---|---|---|---|
| 192.168.95.127 | 11,708 | 3/3 | chain7: only 58/63 chips |
| 192.168.95.181 | 9,052 | 3/3 | chain8: only 46/63 chips |

---

## ZERO HASHRATE (1)

All 3 chains detected with chips but producing 0 GH/s — likely just booted and not yet hashing.

| IP | Chains | Freq | Temp |
|---|---|---|---|
| 192.168.95.245 | 3/3 | 550 | 51°C |

---

## OTHER ISSUES (2)

Mining but pool connection dead.

| IP | GH/s | Issue |
|---|---|---|
| 192.168.95.87 | 11,063 | Pool status: Dead |
| 192.168.95.155 | 8,160 | Pool status: Dead, corrupted freq config |

---

## HEALTHY MINERS (80)

Full list of miners operating normally with 3/3 chains.

| IP | GH/s | Freq | Temp | Pool |
|---|---|---|---|---|
| 192.168.95.12 | 12,922 | 625 | 83°C | stratum.braiins.com:3333 |
| 192.168.95.15 | 12,817 | 625 | 82°C | stratum.braiins.com:3333 |
| 192.168.95.27 | 12,887 | — | 81°C | stratum.braiins.com:3333 |
| 192.168.95.31 | 12,198 | 625 | 62°C | stratum.braiins.com:3333 |
| 192.168.95.32 | 13,436 | 625 | 80°C | stratum.braiins.com:3333 |
| 192.168.95.33 | 12,865 | 625 | 82°C | stratum.braiins.com:3333 |
| 192.168.95.34 | 13,418 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.35 | 13,247 | 650 | 83°C | stratum.braiins.com:3333 |
| 192.168.95.36 | 12,893 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.37 | 13,379 | 625 | 82°C | stratum.braiins.com:3333 |
| 192.168.95.40 | 13,410 | 625 | 82°C | stratum.braiins.com:3333 |
| 192.168.95.41 | 12,846 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.43 | 13,541 | 625 | 80°C | stratum.braiins.com:3333 |
| 192.168.95.45 | 13,440 | 625 | 79°C | stratum.braiins.com:3333 |
| 192.168.95.46 | 13,505 | 625 | 78°C | stratum.braiins.com:3333 |
| 192.168.95.49 | 13,339 | 625 | 80°C | stratum.braiins.com:3333 |
| 192.168.95.50 | 13,396 | 625 | 82°C | stratum.braiins.com:3333 |
| 192.168.95.51 | 13,458 | 625 | 80°C | stratum.braiins.com:3333 |
| 192.168.95.52 | 8,946 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.53 | 13,366 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.54 | 13,402 | 625 | 82°C | stratum.braiins.com:3333 |
| 192.168.95.55 | 12,918 | 650 | 80°C | solo.antpool.com:3333 |
| 192.168.95.62 | 13,887 | 650 | 82°C | stratum.braiins.com:3333 |
| 192.168.95.63 | 13,996 | 650 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.65 | 14,003 | 650 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.66 | 13,313 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.68 | 13,302 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.69 | 13,350 | 625 | 82°C | stratum.braiins.com:3333 |
| 192.168.95.73 | 13,995 | 650 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.74 | 13,540 | 625 | 81°C | 192.168.95.6:3333 |
| 192.168.95.78 | 13,480 | 625 | 81°C | 192.168.95.6:3333 |
| 192.168.95.88 | 13,449 | 625 | 80°C | stratum.braiins.com:3333 |
| 192.168.95.93 | 13,487 | 625 | 80°C | 192.168.95.6:3333 |
| 192.168.95.95 | 13,967 | 625 | 81°C | 192.168.95.6:3333 |
| 192.168.95.96 | 13,377 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.98 | 10,766 | 500 | 66°C | stratum.braiins.com:3333 |
| 192.168.95.99 | 14,007 | 650 | 82°C | stratum.braiins.com:3333 |
| 192.168.95.100 | 13,263 | 625 | 81°C | 192.168.95.6:3333 |
| 192.168.95.101 | 13,287 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.102 | 13,397 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.103 | 13,532 | 625 | 81°C | 192.168.95.6:3333 |
| 192.168.95.104 | 13,773 | 625 | 82°C | 192.168.95.6:3333 |
| 192.168.95.107 | 13,541 | 625 | 81°C | 192.168.95.6:3333 |
| 192.168.95.108 | 13,238 | 625 | 81°C | 192.168.95.6:3333 |
| 192.168.95.112 | 12,836 | 625 | 81°C | 172.65.65.63:3333 |
| 192.168.95.115 | 13,397 | 625 | 79°C | 192.168.95.6:3333 |
| 192.168.95.121 | 13,421 | 625 | 80°C | 192.168.95.6:3333 |
| 192.168.95.137 | 12,926 | 625 | 81°C | 192.168.95.6:3333 |
| 192.168.95.140 | 10,828 | 625 | 82°C | stratum.braiins.com:3333 |
| 192.168.95.142 | 13,390 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.148 | 13,224 | 625 | 81°C | 192.168.95.6:3333 |
| 192.168.95.156 | 13,437 | 625 | 80°C | stratum.braiins.com:3333 |
| 192.168.95.158 | 13,861 | 650 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.163 | 13,428 | 625 | 80°C | stratum.braiins.com:3333 |
| 192.168.95.164 | 13,475 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.174 | 12,785 | 625 | 80°C | 192.168.95.6:3333 |
| 192.168.95.176 | 12,884 | — | 81°C | stratum.braiins.com:3333 |
| 192.168.95.178 | 14,250 | 625 | 81°C | 192.168.95.6:3333 |
| 192.168.95.185 | 8,914 | 625 | 80°C | 192.168.95.6:3333 |
| 192.168.95.186 | 13,824 | 650 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.187 | 13,402 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.188 | 13,398 | 625 | 80°C | stratum.braiins.com:3333 |
| 192.168.95.192 | 12,713 | 625 | 68°C | 192.168.95.6:3333 |
| 192.168.95.195 | 7,444 | 625 | 45°C | 192.168.95.6:3333 |
| 192.168.95.196 | 13,384 | 625 | 75°C | stratum.braiins.com:3333 |
| 192.168.95.197 | 13,026 | 625 | 65°C | 192.168.95.6:3333 |
| 192.168.95.202 | 13,349 | 625 | 81°C | 192.168.95.6:3333 |
| 192.168.95.204 | 13,425 | 625 | 81°C | 192.168.95.6:3333 |
| 192.168.95.208 | 13,410 | 625 | 80°C | stratum.braiins.com:3333 |
| 192.168.95.214 | 9,147 | 625 | 82°C | 192.168.95.6:3333 |
| 192.168.95.216 | 12,944 | 650 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.217 | 12,879 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.225 | 13,193 | 625 | 80°C | 192.168.95.6:3333 |
| 192.168.95.229 | 13,604 | 625 | 80°C | 192.168.95.6:3333 |
| 192.168.95.230 | 13,417 | 625 | 81°C | stratum.braiins.com:3333 |
| 192.168.95.232 | 8,774 | 625 | 82°C | 192.168.95.6:3333 |
| 192.168.95.233 | 14,080 | 625 | 82°C | 192.168.95.6:3333 |
| 192.168.95.234 | 11,811 | 550 | 80°C | stratum.braiins.com:3333 |
| 192.168.95.239 | 13,333 | 625 | 80°C | 192.168.95.6:3333 |
| 192.168.95.243 | 13,621 | 625 | 82°C | 192.168.95.6:3333 |

---

## Pool Config Breakdown (all 173 miners)

| Pool URL | Count |
|---|---|
| stratum.braiins.com:3333 | 114 |
| 192.168.95.6:3333 (fake stratum) | 51 |
| Unknown (unreachable) | 5 |
| 172.65.65.63:3333 | 2 |
| solo.antpool.com:3333 | 1 |

---

## Firmware Breakdown

| Firmware | Count |
|---|---|
| Antminer S9 (vnish 3.9.0) | 105 |
| Antminer S9 (no vnish in name) | 58 |
| Antminer S9i | 5 |
| Unknown (unreachable) | 5 |
