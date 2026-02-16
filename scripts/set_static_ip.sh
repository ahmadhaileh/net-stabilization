#!/bin/bash
# Set static IP 192.168.95.6 on enp2s0 via NetworkManager
# Gateway: 192.168.95.2, Subnet: 255.255.255.0 (/24)
nmcli con mod "Wired connection 1" ipv4.method manual ipv4.addresses 192.168.95.6/24 ipv4.gateway 192.168.95.2 ipv4.dns "8.8.8.8 8.8.4.4"
echo "Config saved, reactivating..."
nmcli con up "Wired connection 1"
echo "Static IP set to 192.168.95.6 (gw 192.168.95.2)"
