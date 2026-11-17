#!/usr/bin/python3.8
"""QDC507 SMS multi-contact sender; queue data stays on /volume2.

--check: read-only registration/queue inspection
--test: send a synthetic SIP MESSAGE to each unexpired Contact, no queue writes
(no flags): deliver pending queue SMS via per-target dynamic dialplan and status handshake
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

BASE = Path('/volume2/docker/asterisk/qdc507-current/v2/sms-queue')
PENDING = BASE / 'pending'
DELIVERED = BASE / 'delivered'
WORK = BASE / 'work'
CONTAINER_WORK = '/var/spool/asterisk/sms-queue/work'
DOCKER = '/usr/local/bin/docker'
CONTAINER = 'qdc507-hardware'
# Publishing-safe placeholder. On an actual deployment, configure the domain
# to match the existing SIP MESSAGE From/Request-URI routing.
SMS_DOMAIN = os.environ.get('QDC507_SMS_DOMAIN', 'example.invalid')
URI_RE = re.compile(r'^sip:100@[A-Za-z0-9.\-:\[\]]+(?:;[A-Za-z0-9_.~:%+\-/=]+)*$')
QID_RE = re.compile(r'^[A-Za-z0-9_.-]{1,160}$')
MAX_CONTACTS = 10
WAIT_SECONDS = 15


def ast(command):
    result = subprocess.run(
        [DOCKER, 'exec', CONTAINER, 'asterisk', '-rx', command],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=25, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError('ASTERISK_CLI_EXIT_{}: {}'.format(
            result.returncode, (result.stderr or result.stdout).strip()[:250]))
    return result.stdout.strip()


def contacts_now():
    raw = ast('database show registrar/contact')
    now = int(time.time())
    contacts = []
    seen = set()
    for line in raw.splitlines():
        if not line.startswith('/registrar/contact/100;@') or ': ' not in line:
            continue
        try:
            obj = json.loads(line.split(': ', 1)[1])
            if int(obj.get('expiration_time', 0)) <= now:
                continue
            uri = obj.get('uri', '')
            if not isinstance(uri, str) or not URI_RE.fullmatch(uri):
                print('CONTACT_SKIPPED_INVALID_URI=1')
                continue
            if uri in seen:
                continue
            seen.add(uri)
            ua = str(obj.get('user_agent', ''))
            label = ('SIPIS' if 'SIPIS' in ua.upper() else
                     'DIRECT' if 'GROUNDWIRE' in ua.upper() else 'OTHER')
            contacts.append((uri, label))
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    if len(contacts) > MAX_CONTACTS:
        raise RuntimeError('TOO_MANY_CONTACTS_{}'.format(len(contacts)))
    return sorted(contacts, key=lambda item: (item[1] != 'DIRECT', item[0]))


def safe_from(gw, src, conv):
    # Keep the original GW/SRC display-name and conversation URI layout.
    if not re.fullmatch(r'[A-Za-z0-9_+.-]{1,100}', conv):
        raise RuntimeError('UNSAFE_CONVERSATION_ID')
    display = (gw + ' ' + src).replace('"', "'")
    display = re.sub(r'[\r\n,<>\\]', '_', display)[:160]
    return '"{}"<sip:{}@{}>'.format(display, conv, SMS_DOMAIN)


def stage_files(data, uri):
    WORK.mkdir(parents=True, exist_ok=True)
    owner = WORK.stat()
    stage = WORK / ('multiv2-' + uuid.uuid4().hex)
    stage.mkdir(mode=0o770)
    os.chown(str(stage), owner.st_uid, owner.st_gid)
    stage.chmod(0o770)
    for name in ('body', 'from', 'uri'):
        path = stage / name
        path.write_text(data[name] if name != 'uri' else uri, encoding='utf-8')
        os.chown(str(path), owner.st_uid, owner.st_gid)
        path.chmod(0o660)
    status = stage / 'status'
    status.write_text('', encoding='ascii')
    os.chown(str(status), owner.st_uid, owner.st_gid)
    status.chmod(0o660)
    return stage


def send_one(data, uri):
    stage = stage_files(data, uri)
    ctx = 'smsmv2_{}_{}'.format(os.getpid(), uuid.uuid4().hex[:12])
    container_path = CONTAINER_WORK + '/' + stage.name
    steps = [
        'Set(MESSAGE(body)=${{FILE({}/body)}})'.format(container_path),
        'Set(DIRECTURI=${{FILE({}/uri)}})'.format(container_path),
        'Set(FROMHDR=${{FILE({}/from)}})'.format(container_path),
        'MessageSend(pjsip:100/${DIRECTURI},${FROMHDR},sip:100@' + SMS_DOMAIN + ')',
        'Set(FILE({}/status)=${{MESSAGE_SEND_STATUS}})'.format(container_path),
        'Hangup()',
    ]
    started = False
    try:
        for number, step in enumerate(steps, 1):
            response = ast('dialplan add extension s,{},{} into {}'.format(
                number, step, ctx))
            if 'added into' not in response.lower():
                raise RuntimeError('DIALPLAN_ADD_FAILED: {}'.format(response[:200]))
        ast('channel originate Local/s@{} application Wait 1'.format(ctx))
        started = True
        deadline = time.monotonic() + WAIT_SECONDS
        statusfile = stage / 'status'
        while time.monotonic() < deadline:
            text = statusfile.read_text(encoding='ascii', errors='replace').strip()
            if text in ('SUCCESS', 'FAILURE', 'INVALID_URI', 'INVALID_PROTOCOL'):
                return text
            time.sleep(0.15)
        return 'TIMEOUT'
    finally:
        try:
            ast('dialplan remove context {}'.format(ctx))
        except Exception as exc:
            print('DIALPLAN_CLEANUP_WARNING={}'.format(str(exc)[:100]))
        if started and not (stage / 'status').read_text(encoding='ascii').strip():
            # Preserve timed-out transaction for investigation; avoid deleting
            # files while a delayed Asterisk channel could still be reading them.
            print('STAGE_RETAINED_FOR_TIMEOUT={}'.format(stage.name))
        else:
            shutil.rmtree(str(stage))


def atomic_json(path, obj):
    fd, tmp = tempfile.mkstemp(prefix='.smsmv2-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(obj, out, ensure_ascii=False, sort_keys=True, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def queue_count():
    return len(list(PENDING.glob('*.body'))) if PENDING.exists() else 0


def run_test(contacts):
    if not contacts:
        print('NO_CONTACTS_TEST_SKIPPED=1')
        return 2
    tag = time.strftime('%Y%m%d_%H%M%S')
    all_ok = True
    for index, (uri, label) in enumerate(contacts, 1):
        text = 'NAS_SMS_MULTIV2_TEST_{}_PATH={}_OF_{}_{}'.format(
            tag, index, len(contacts), label)
        print('TEST_MESSAGE={}'.format(text))
        outcome = send_one({'body': text,
                            'from': '"NAS SMS V2 TEST"<sip:710086@' + SMS_DOMAIN + '>'}, uri)
        print('TEST_PATH_{}_STATUS={}'.format(index, outcome))
        all_ok = all_ok and outcome == 'SUCCESS'
    print('TEST_PROTOCOL_HANDLER_ALL_SUCCESS={}'.format(int(all_ok)))
    print('PHONE_RECEIPT_NOT_CONFIRMED=1')
    return 0 if all_ok else 3


def process(contacts):
    if not contacts:
        print('NO_UNEXPIRED_CONTACTS_PENDING_RETAINED={}'.format(queue_count()))
        return
    PENDING.mkdir(parents=True, exist_ok=True)
    DELIVERED.mkdir(parents=True, exist_ok=True)
    for bodyfile in sorted(PENDING.glob('*.body')):
        qid = bodyfile.stem
        if not QID_RE.fullmatch(qid):
            print('UNSAFE_QID_SKIPPED=1')
            continue
        paths = {key: PENDING / '{}.{}'.format(qid, key)
                 for key in ('body', 'gw', 'src', 'conv')}
        # Recovery from interruption after successful archive, before cleanup.
        if (DELIVERED / qid / 'multiv2.json').is_file():
            if all(p.is_file() for p in paths.values()):
                for p in paths.values():
                    p.unlink()
                statefile = PENDING / (qid + '.multiv2.json')
                if statefile.exists():
                    statefile.unlink()
                print('QID={} PREVIOUSLY_ARCHIVED_PENDING_CLEANED=1'.format(qid))
            continue
        if not all(p.is_file() for p in paths.values()):
            print('INCOMPLETE_QID_SKIPPED={}'.format(qid))
            continue
        statepath = PENDING / (qid + '.multiv2.json')
        try:
            state = json.loads(statepath.read_text(encoding='utf-8')) \
                if statepath.exists() else {'version': 2, 'ok_uris': [], 'attempts': []}
            if state.get('version') != 2:
                raise RuntimeError('STATE_VERSION_MISMATCH')
            ok_uris = set(state.get('ok_uris', []))
            gw = paths['gw'].read_text(encoding='utf-8').strip()
            src = paths['src'].read_text(encoding='utf-8').strip()
            conv = paths['conv'].read_text(encoding='utf-8').strip()
            data = {'body': paths['body'].read_text(encoding='utf-8'),
                    'from': safe_from(gw, src, conv)}
            for index, (uri, label) in enumerate(contacts, 1):
                if uri in ok_uris:
                    continue
                outcome = send_one(data, uri)
                print('QID={} PATH={} TYPE={} STATUS={}'.format(
                    qid, index, label, outcome))
                state['attempts'].append({
                    'time': time.strftime('%F %T %z'),
                    'uri': uri, 'type': label, 'send_status': outcome})
                if outcome == 'SUCCESS':
                    ok_uris.add(uri)
                state['ok_uris'] = sorted(ok_uris)
                atomic_json(statepath, state)
            # SUCCESS only proves acceptance by Asterisk's protocol handler.
            if not all(uri in ok_uris for uri, _ in contacts):
                print('QID={} PENDING_RETAINED=1'.format(qid))
                continue
            dest = DELIVERED / qid
            if dest.exists():
                raise RuntimeError('ARCHIVE_DESTINATION_ALREADY_EXISTS')
            temp_dest = DELIVERED / ('.multiv2-' + uuid.uuid4().hex)
            temp_dest.mkdir(mode=0o700)
            try:
                # Copy first: never delete pending files before a complete archive
                # is durably visible under delivered/<qid>.
                for key, path in paths.items():
                    shutil.copy2(str(path), str(temp_dest / key))
                (temp_dest / 'uri').write_text(
                    '\n'.join(sorted(ok_uris)) + '\n', encoding='utf-8')
                (temp_dest / 'delivered_at').write_text(
                    time.strftime('%F %T %z'), encoding='ascii')
                state['archive_meaning'] = 'SIP handler accepted; phone unconfirmed'
                atomic_json(temp_dest / 'multiv2.json', state)
                os.replace(str(temp_dest), str(dest))
            finally:
                if temp_dest.exists():
                    shutil.rmtree(str(temp_dest))
            # Once archive is present, remove pending. If interrupted here,
            # the next run recognizes delivered/<qid> and does not resend.
            for path in paths.values():
                path.unlink()
            if statepath.exists():
                statepath.unlink()
            print('QID={} ARCHIVED_PROTOCOL_HANDOFF=1 PHONE_UNCONFIRMED=1'.format(qid))
        except Exception as exc:
            print('QID={} ERROR={}'.format(qid, str(exc)[:200]))
            # Keep this and other messages in pending for retry on next tick.
            raise


def main():
    ap = argparse.ArgumentParser()
    gp = ap.add_mutually_exclusive_group()
    gp.add_argument('--check', action='store_true')
    gp.add_argument('--test', action='store_true')
    args = ap.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('ROOT_REQUIRED')
    contacts = contacts_now()
    print('CONTACT_COUNT={}'.format(len(contacts)))
    for i, (uri, label) in enumerate(contacts, 1):
        print('CONTACT_{}={} {}'.format(i, label, uri))
    print('PENDING_COUNT={}'.format(queue_count()))
    if args.check:
        print('READ_ONLY_CHECK_OK=1')
        return 0
    if args.test:
        return run_test(contacts)
    process(contacts)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('SMS_MULTIV2_FATAL={}'.format(str(exc)[:200]))
        raise SystemExit(2)
