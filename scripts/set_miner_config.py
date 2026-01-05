#!/usr/bin/env python3
"""
Set miner config from template.
Usage: python3 set_miner_config.py <miner_ip> [--freq 500] [--voltage 860]
"""
import sys
import json
import requests
from requests.auth import HTTPDigestAuth

def load_template():
    with open('data/vnish_config_template.json') as f:
        return json.load(f)

def build_form_data(config):
    """Build form data in exact order vnish expects."""
    c = config
    p = c['pools']
    f = c['frequency']
    v = c['voltage']
    fan = c['fan']
    auto = c['autodownscale']
    m = c['misc']
    
    # Order matters!
    return [
        ('_ant_pool1url', p['pool1_url']),
        ('_ant_pool1user', p['pool1_user']),
        ('_ant_pool1pw', p['pool1_pass']),
        ('_ant_pool2url', p['pool2_url']),
        ('_ant_pool2user', p['pool2_user']),
        ('_ant_pool2pw', p['pool2_pass']),
        ('_ant_pool3url', p['pool3_url']),
        ('_ant_pool3user', p['pool3_user']),
        ('_ant_pool3pw', p['pool3_pass']),
        ('_ant_nobeeper', str(m['nobeeper']).lower()),
        ('_ant_notempoverctrl', str(m['notempoverctrl']).lower()),
        ('_ant_fan_customize_switch', str(fan['customize_switch']).lower()),
        ('_ant_fan_customize_value', str(fan['customize_value'])),
        ('_ant_freq', str(f['global'])),
        ('_ant_freq1', str(f['chain1'])),
        ('_ant_freq2', str(f['chain2'])),
        ('_ant_freq3', str(f['chain3'])),
        ('_ant_voltage', str(v['global'])),
        ('_ant_voltage1', str(v['chain1'])),
        ('_ant_voltage2', str(v['chain2'])),
        ('_ant_voltage3', str(v['chain3'])),
        ('_ant_fan_rpm_off', str(fan['rpm_off'])),
        ('_ant_chip_freq', c.get('chip_freq', '')),
        ('_ant_autodownscale', str(auto['enabled']).lower()),
        ('_ant_autodownscale_watch', str(auto['watch']).lower()),
        ('_ant_autodownscale_watchtimer', str(auto['watchtimer']).lower()),
        ('_ant_autodownscale_timer', str(auto['timer'])),
        ('_ant_autodownscale_after', str(auto['after'])),
        ('_ant_autodownscale_step', str(auto['step'])),
        ('_ant_autodownscale_min', str(auto['min'])),
        ('_ant_autodownscale_prec', str(auto['prec'])),
        ('_ant_autodownscale_profile', str(auto['profile'])),
        ('_ant_minhr', str(c['minhr'])),
        ('_ant_asicboost', str(m['asicboost']).lower()),
        ('_ant_tempoff', str(m['tempoff'])),
        ('_ant_altdf', str(m['altdf']).lower()),
        ('_ant_presave', str(m['presave'])),
        ('_ant_name', str(m['name'])),
        ('_ant_warn', m['warn']),
        ('_ant_maxx', m['maxx']),
        ('_ant_trigger_reboot', m['trigger_reboot']),
        ('_ant_target_temp', str(m['target_temp'])),
        ('_ant_silentstart', str(m['silentstart']).lower()),
        ('_ant_altdfno', str(m['altdfno'])),
        ('_ant_autodownscale_reboot', str(auto['reboot']).lower()),
        ('_ant_hotel_fee', str(m['hotel_fee']).lower()),
        ('_ant_lpm_mode', str(m['lpm_mode']).lower()),
        ('_ant_dchain5', str(m['dchain5']).lower()),
        ('_ant_dchain6', str(m['dchain6']).lower()),
        ('_ant_dchain7', str(m['dchain7']).lower()),
    ]

def set_config(ip, config, restart=True):
    auth = HTTPDigestAuth('root', 'root')
    url = f'http://{ip}/cgi-bin/set_miner_conf_custom.cgi'
    
    form_data = build_form_data(config)
    resp = requests.post(url, auth=auth, data=form_data, timeout=30)
    
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    
    if restart:
        try:
            requests.get(f'http://{ip}/cgi-bin/reboot_cgminer.cgi', 
                        auth=auth, timeout=2)
        except:
            pass  # Expected - endpoint never responds
    
    return True, resp.text

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 set_miner_config.py <ip> [--freq N] [--voltage N]")
        sys.exit(1)
    
    ip = sys.argv[1]
    config = load_template()
    
    # Parse args
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == '--freq' and i+1 < len(sys.argv):
            config['frequency']['global'] = int(sys.argv[i+1])
            i += 2
        elif sys.argv[i] == '--voltage' and i+1 < len(sys.argv):
            config['voltage']['global'] = int(sys.argv[i+1])
            i += 2
        else:
            i += 1
    
    print(f"Setting config on {ip}:")
    print(f"  Frequency: {config['frequency']['global']} MHz")
    print(f"  Voltage: {config['voltage']['global']} (={config['voltage']['global']/100}V)")
    
    ok, msg = set_config(ip, config)
    print(f"Result: {'OK' if ok else 'FAILED'} - {msg}")

if __name__ == '__main__':
    main()
