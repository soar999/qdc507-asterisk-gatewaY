#!/bin/sh
case "$0" in
 *d4) ns='4p 4c'; [ "${QDC_FAKE_FAIL_D4:-0}" = 0 ] || exit 42;;
 *d5) ns='5p 6c';;
esac
close_pcm() {
 for n in $ns; do
  echo closed > "$QDC_TEST_PCM_ROOT/pcm$n/sub0/hw_params"
  echo 'state: CLOSED' > "$QDC_TEST_PCM_ROOT/pcm$n/sub0/status"
 done
 exit 0
}
trap close_pcm TERM INT HUP
for n in $ns; do
 echo 'rate: 16000' > "$QDC_TEST_PCM_ROOT/pcm$n/sub0/hw_params"
 echo 'state: RUNNING' > "$QDC_TEST_PCM_ROOT/pcm$n/sub0/status"
done
while :; do sleep 0.1; done
