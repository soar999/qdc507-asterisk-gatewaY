# QDC507 registration state fix

Do not let a circuit-domain CREG failure erase a successful LTE CEREG registration.

## Problem

After Asterisk restarts, the modem is not restarted. The driver queried CREG, received stat 3, and stored "not registered". It enabled CEREG unsolicited reports but did not query the current CEREG state. Because LTE registration had not changed, the modem did not send a new CEREG report.

The module was already registered for LTE/VoLTE, but the driver refused outgoing calls.

## Required behavior

- Query both CREG and CEREG during driver initialization.
- Store the two results independently.
- Treat the device as usable when either domain is registered.
- Do not add periodic registration polling.

## Files

- `at_command.c`: add `AT+CEREG?` beside the existing startup `AT+CREG?`.
- `at_response.c`: update only the domain that sent the response, then set `gsm_registered` from either success.
- `chan_quectel.h`: add `lte_reg_status`, `creg_registered`, and `cereg_registered`.
- `chan_quectel.c`: clear both domains on disconnect and expose LTE status.
- `cli.c`: show `LTE Registration Status`.

The tested state after restart was:

- CREG: Registration denied
- CEREG: Registered, home network
- Device state: Free
