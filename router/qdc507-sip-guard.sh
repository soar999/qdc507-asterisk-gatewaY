#!/bin/sh

PATH=/sbin:/bin:/usr/sbin:/usr/bin

NAS_IP="192.168.1.110"
SIP_PORT="7777"
CHAIN="QDC507_SIP_GUARD"

# Load required netfilter modules if available.
modprobe xt_hashlimit 2>/dev/null || true
modprobe xt_string 2>/dev/null || true

# Create/rebuild our private chain.
iptables -N "$CHAIN" 2>/dev/null || true
iptables -F "$CHAIN"

# Drop only excessive REGISTER requests, counted independently per source IP.
iptables -A "$CHAIN" \
  -p udp --dport "$SIP_PORT" \
  -m string --algo bm --string 'REGISTER' \
  -m hashlimit \
  --hashlimit-mode srcip \
  --hashlimit-name qdc_reg_udp \
  --hashlimit-above 60/minute \
  --hashlimit-burst 20 \
  -j DROP

# All other SIP traffic continues normally.
iptables -A "$CHAIN" -j RETURN

# Remove duplicate hooks if this script runs more than once.
while iptables -C FORWARD \
  -p udp -d "$NAS_IP" --dport "$SIP_PORT" \
  -j "$CHAIN" 2>/dev/null
do
  iptables -D FORWARD \
    -p udp -d "$NAS_IP" --dport "$SIP_PORT" \
    -j "$CHAIN"
done

# Examine traffic after DNAT: WAN :7777 -> NAS :7777.
iptables -I FORWARD 1 \
  -p udp -d "$NAS_IP" --dport "$SIP_PORT" \
  -j "$CHAIN"
