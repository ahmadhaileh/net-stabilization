#!/bin/bash
# Set static IP 192.168.95.2 on enp2s0 via NetworkManager
nmcli con mod "Wired connection 1" ipv4.method manual ipv4.addresses 192.168.95.2/24 ipv4.gateway 192.168.95.1 ipv4.dns "8.8.8.8 8.8.4.4"
echo "Config saved, reactivating..."
nmcli con up "Wired connection 1"
echo "Static IP set to 192.168.95.2"
