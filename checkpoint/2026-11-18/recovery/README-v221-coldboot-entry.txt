QDC507 16K memoryfix v2.2 staged NAS startup entry v2.2.1 (Nov 18, 2026)

Purpose: Make coldboot logs remain on /volume2, and start one v2 watchdog
only after successful coldboot and Guard16 post-boot validation.

Three files; install to /volume2/docker/asterisk/qdc507-16k-lab/ ROOT.
Extraction does NOT install a DSM startup task, change Asterisk, or restart USB.
The original qdc507-coldboot-memoryfix-v22.py is intentionally NOT changed.

Read-only verification on running NAS:
/usr/bin/python3.8 -I -B /volume2/docker/asterisk/qdc507-16k-lab/qdc507-coldboot-startup-v221.py --selftest
/usr/bin/python3.8 -I -B /volume2/docker/asterisk/qdc507-16k-lab/qdc507-coldboot-startup-v221.py --check

DO NOT EXECUTE --boot until a planned controlled first coldboot test. It can stop
the SMS timer, change USB device bindings, refresh ADB, and start a container.

After validation, configure ONE DSM Task Scheduler 'Triggered Task' / 'Boot-up',
run as root, with bounded mount readiness wait then the script --boot.
Avoid enabling redundant hotplug timer or extra watchdog.
This startup wrapper is not an additional proof that real coldboot works.

Safety: retains v22 exact core logic except replaces one /var/lib ADB log path
with /volume2 LAB. New watcher starts only after successful coldboot + guard.
