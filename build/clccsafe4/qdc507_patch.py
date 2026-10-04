from pathlib import Path

# ============================================================
# 1. QDC507 external UAC: slin48 -> slin 8kHz
# ============================================================
p = Path("/usr/src/asterisk-chan-quectel/src/chan_quectel.c")
s = p.read_text()

start = s.index("const struct ast_format* pvt_get_audio_format")
end = s.index("static size_t pvt_get_audio_frame_size_r", start)
block = s[start:end]

needle = "return ast_format_slin48;"
if block.count(needle) != 2:
    raise RuntimeError(
        f"Unexpected slin48 count: {block.count(needle)}"
    )

pos = block.rfind(needle)
block = block[:pos] + "return ast_format_slin;" + block[pos + len(needle):]
s = s[:start] + block + s[end:]
p.write_text(s)

print("QDC507: external UAC set to 8 kHz")


# ============================================================
# 2. Baiwang/QDC507 is voice capable
# ============================================================
p = Path("/usr/src/asterisk-chan-quectel/src/at_response.c")
s = p.read_text()

start = s.index("static int at_response_cgmi")
end = s.index("static int at_response_cgmm", start)
block = s[start:end]

needle = "pvt->has_voice = 0;"
if block.count(needle) != 3:
    raise RuntimeError(
        f"Unexpected has_voice count: {block.count(needle)}"
    )

pos = block.rfind(needle)
replacement = (
    'pvt->has_voice = '
    '!strncasecmp(ast_str_buffer(response), "Baiwang", 7);'
)

block = block[:pos] + replacement + block[pos + len(needle):]
s = s[:start] + block + s[end:]
p.write_text(s)

print("QDC507: Baiwang marked voice capable")


# ============================================================
# 3. External UAC: do NOT link capture and playback
# ============================================================
p = Path("/usr/src/asterisk-chan-quectel/src/chan_quectel.c")
s = p.read_text()

old = '''    const int err = snd_pcm_link(pvt->icard, pvt->ocard);
    if (err < 0) {
        ast_log(LOG_ERROR, "[%s][ALSA] Couldn't link devices: %s\\n", PVT_ID(pvt), snd_strerror(err));
        snd_pcm_close(pvt->icard);
        pvt->icard = NULL;
        snd_pcm_close(pvt->ocard);
        pvt->ocard          = NULL;
        pvt->ocard_channels = 0u;
        return -1;
    }
'''

new = '''    /*
     * QDC507 external UAC exposes independent capture/playback USB
     * endpoints.  Do not link them: starting capture while playback
     * is empty causes an immediate playback XRUN on this device.
     */
    if (CONF_UNIQ(pvt, uac) != TRIBOOL_NONE) {
        const int err = snd_pcm_link(pvt->icard, pvt->ocard);
        if (err < 0) {
            ast_log(LOG_ERROR, "[%s][ALSA] Couldn't link devices: %s\\n",
                    PVT_ID(pvt), snd_strerror(err));
            snd_pcm_close(pvt->icard);
            pvt->icard = NULL;
            snd_pcm_close(pvt->ocard);
            pvt->ocard          = NULL;
            pvt->ocard_channels = 0u;
            return -1;
        }
    } else {
        ast_verb(2,
                 "[%s][ALSA] QDC507 external UAC: capture/playback unlinked\\n",
                 PVT_ID(pvt));
    }
'''

if old not in s:
    raise RuntimeError("Cannot locate snd_pcm_link block")

s = s.replace(old, new, 1)
p.write_text(s)

print("QDC507: capture/playback unlinked")


# ============================================================
# 4. Start only CAPTURE when call activates
# ============================================================
p = Path("/usr/src/asterisk-chan-quectel/src/cpvt.c")
s = p.read_text()

start = s.index("void cpvt_call_activate")
end = s.index("int cpvt_control", start)
block = s[start:end]

marker = '''    if (pvt->audio_fd >= 0) {
'''

insert = '''    /*
     * QDC507 external UAC:
     * capture must run before Asterisk's audio fd becomes readable.
     * Playback is deliberately NOT explicitly started; ALSA starts it
     * when Asterisk supplies PCM data.
     */
    if (CONF_UNIQ(pvt, uac) == TRIBOOL_NONE && pvt->icard) {
        int err;

        snd_pcm_drop(pvt->icard);

        err = snd_pcm_prepare(pvt->icard);
        if (err < 0) {
            ast_log(LOG_ERROR,
                    "[%s][ALSA][CAPTURE] Prepare failed: %s\\n",
                    PVT_ID(pvt), snd_strerror(err));
            return;
        }

        err = snd_pcm_start(pvt->icard);
        if (err < 0) {
            ast_log(LOG_ERROR,
                    "[%s][ALSA][CAPTURE] Start failed: %s\\n",
                    PVT_ID(pvt), snd_strerror(err));
            return;
        }

        ast_verb(2,
                 "[%s][ALSA] QDC507 capture started\\n",
                 PVT_ID(pvt));
    }

'''

if marker not in block:
    raise RuntimeError("Cannot find cpvt activation point")

block = block.replace(marker, insert + marker, 1)
s = s[:start] + block + s[end:]


# ============================================================
# 5. Stop both independent streams when call ends
# ============================================================
start = s.index("void cpvt_call_disactivate")
end = s.index("void cpvt_call_activate", start)
block = s[start:end]

old = '''    if (CONF_UNIQ(pvt, uac) > TRIBOOL_FALSE) {
        // snd_pcm_drop(pvt->icard);
        // snd_pcm_drop(pvt->ocard);
    } else {
'''

new = '''    if (CONF_UNIQ(pvt, uac) > TRIBOOL_FALSE) {
        if (pvt->icard) {
            snd_pcm_drop(pvt->icard);
        }
        if (pvt->ocard) {
            snd_pcm_drop(pvt->ocard);
        }
    } else {
'''

if old not in block:
    raise RuntimeError("Cannot find UAC deactivate block")

block = block.replace(old, new, 1)
s = s[:start] + block + s[end:]

p.write_text(s)

print("QDC507: capture start / independent stop installed")
print("ALL QDC507 PATCHES APPLIED")


# ============================================================
# 6. QDC507 CLCC generation bitmap + release disappeared calls
# ============================================================

from pathlib import Path


# ------------------------------------------------------------
# 6a. Add a 32-bit bitmap to pvt.
# call_idx is 0..31.
# ------------------------------------------------------------
p = Path("/usr/src/asterisk-chan-quectel/src/chan_quectel.h")
s = p.read_text()

needle = "    int data_fd;"
pos = s.find(needle)
if pos < 0:
    raise RuntimeError("QDC507 CLCC: cannot find data_fd")

eol = s.find("\n", pos)
if eol < 0:
    raise RuntimeError("QDC507 CLCC: malformed data_fd line")

insert = "    uint32_t qdc507_clcc_seen; /*!< QDC507 calls seen in current CLCC generation */\n"

if "qdc507_clcc_seen" not in s:
    s = s[:eol + 1] + insert + s[eol + 1:]

p.write_text(s)
print("QDC507 CLCC: bitmap field installed")


# ------------------------------------------------------------
# 6b. Every valid VOICE +CLCC line marks its call_idx as seen.
#
# Do not depend on CALL_FLAG_ALIVE at all.
# ------------------------------------------------------------
p = Path("/usr/src/asterisk-chan-quectel/src/at_response.c")
s = p.read_text()

fn_start = s.find("static int at_response_clcc(")
if fn_start < 0:
    raise RuntimeError("QDC507 CLCC: cannot find at_response_clcc")

fn_end = s.find("\n}", fn_start)
if fn_end < 0:
    raise RuntimeError("QDC507 CLCC: cannot find end of at_response_clcc")

block = s[fn_start:fn_end]

needle = "        handle_clcc(pvt, call_idx, dir, state, mode,"
pos = block.find(needle)

if pos < 0:
    raise RuntimeError("QDC507 CLCC: cannot find handle_clcc in at_response_clcc")

mark = """        if (call_idx <= MAX_CALL_IDX) {
            pvt->qdc507_clcc_seen |= (1u << call_idx);
        }

"""

block = block[:pos] + mark + block[pos:]
s = s[:fn_start] + block + s[fn_end:]


# ------------------------------------------------------------
# 6c. At trailing OK of AT+CLCC:
#     - anything not seen disappeared
#     - release its Asterisk channel
#     - reset bitmap for next CLCC generation
# ------------------------------------------------------------
marker = "static int at_response_cmgs_error(struct pvt*, const at_queue_task_t* const);"

if marker not in s:
    raise RuntimeError("QDC507 CLCC: cannot find helper insertion marker")

helper = r'''
static void qdc507_clcc_finalize(struct pvt* const pvt)
{
    if (strncmp(pvt->manufacturer, "Baiwang", 7)) {
        pvt->qdc507_clcc_seen = 0u;
        return;
    }

    for (;;) {
        struct cpvt* stale = NULL;
        struct cpvt* cpvt;

        AST_LIST_TRAVERSE(&pvt->chans, cpvt, entry) {
            /*
             * INIT may not yet have received the modem call_idx.
             */
            if (cpvt->state == CALL_STATE_INIT) {
                continue;
            }

            if (cpvt->call_idx < MIN_CALL_IDX ||
                cpvt->call_idx > MAX_CALL_IDX) {
                continue;
            }

            if (!(pvt->qdc507_clcc_seen &
                  (1u << cpvt->call_idx))) {
                stale = cpvt;
                break;
            }
        }

        if (!stale) {
            break;
        }

        const int idx = stale->call_idx;

        ast_verb(2,
                 "[%s][QDC507] CLCC idx:%d disappeared -> RELEASED\n",
                 PVT_ID(pvt), idx);

        /*
         * This may free stale, so restart traversal afterwards.
         */
        cpvt_change_state(stale,
                          CALL_STATE_RELEASED,
                          AST_CAUSE_NORMAL_CLEARING);
    }

    /* next CLCC generation starts empty */
    pvt->qdc507_clcc_seen = 0u;
}

'''

s = s.replace(marker, helper + marker, 1)


# ------------------------------------------------------------
# 6d. Give CMD_AT_CLCC its own final OK handler.
# ------------------------------------------------------------
ok_start = s.find("static int at_response_ok(")
ok_end = s.find("static void __attribute__", ok_start)

if ok_start < 0 or ok_end < 0:
    raise RuntimeError("QDC507 CLCC: cannot locate at_response_ok")

ok = s[ok_start:ok_end]

case = "        case CMD_AT_CLCC:\n"
if ok.count(case) != 1:
    raise RuntimeError(
        "QDC507 CLCC: CMD_AT_CLCC not found exactly once in at_response_ok"
    )

# Remove it from generic OK group.
ok = ok.replace(case, "", 1)

marker2 = "        case CMD_AT_FINAL:\n"
if marker2 not in ok:
    raise RuntimeError("QDC507 CLCC: cannot find CMD_AT_FINAL")

dedicated = """        case CMD_AT_CLCC:
            at_ok_response_dbg(3, pvt, ecmd, NULL);
            qdc507_clcc_finalize(pvt);
            break;

        case CMD_AT_FINAL:
"""

ok = ok.replace(marker2, dedicated, 1)

s = s[:ok_start] + ok + s[ok_end:]
p.write_text(s)

print("QDC507 CLCC: trailing OK finalizer installed")


# ------------------------------------------------------------
# 6e. Make CLI:
#
#   quectel cmd quectel0 AT+CLCC
#
# use real CMD_AT_CLCC, so our temporary polling test exercises
# the same parser/finalizer.
# ------------------------------------------------------------
p = Path("/usr/src/asterisk-chan-quectel/src/at_command.c")
s = p.read_text()

fn = s.find("int at_enqueue_user_cmd(")
if fn < 0:
    raise RuntimeError("QDC507 CLCC: cannot find at_enqueue_user_cmd")

brace = s.find("{", fn)
if brace < 0:
    raise RuntimeError("QDC507 CLCC: malformed at_enqueue_user_cmd")

inject = '''
    if (!strcmp(input, "AT+CLCC") || !strcmp(input, "+CLCC")) {
        return at_enqueue_clcc(cpvt);
    }
'''

s = s[:brace + 1] + inject + s[brace + 1:]
p.write_text(s)

print("QDC507 CLCC: CLI AT+CLCC mapped to real CMD_AT_CLCC")
print("QDC507 CLCC BITMAP PATCH APPLIED SUCCESSFULLY")

# ============================================================
# 7. QDC507 built-in 1-second CLCC polling
# ============================================================

from pathlib import Path

p = Path("/usr/src/asterisk-chan-quectel/src/monitor_thread.c")
s = p.read_text()

# ------------------------------------------------------------
# 7a. Poll task.
#
# It runs through the existing serialized taskprocessor and under
# pvt->lock.  Only enqueue CLCC when:
#   - initialization is complete
#   - manufacturer is Baiwang
#   - AT queue is completely empty
#
# Therefore a slow modem can never accumulate CLCC requests.
# ------------------------------------------------------------
marker = '''static int restart_monitor_taskproc(void* tpdata) { return PVT_TASKPROC_TRYLOCK_AND_EXECUTE(tpdata, restart_monitor); }
'''

insert = r'''
static void qdc507_clcc_poll(struct pvt* const pvt)
{
    if (!pvt->initialized) {
        return;
    }

    if (strncmp(pvt->manufacturer, "Baiwang", 7)) {
        return;
    }

    /*
     * Never stack polling commands behind real AT work.
     * The next idle timeout will try again.
     */
    if (!AST_LIST_EMPTY(&pvt->at_queue)) {
        return;
    }

    if (at_enqueue_clcc(&pvt->sys_chan)) {
        ast_log(LOG_WARNING,
                "[%s][QDC507] Failed to enqueue periodic CLCC\n",
                PVT_ID(pvt));
    }
}

static int qdc507_clcc_poll_taskproc(void* tpdata)
{
    return PVT_TASKPROC_TRYLOCK_AND_EXECUTE(tpdata,
                                           qdc507_clcc_poll);
}

'''

if marker not in s:
    raise RuntimeError(
        "QDC507 poller: cannot find restart_monitor_taskproc"
    )

s = s.replace(marker, marker + insert, 1)


# ------------------------------------------------------------
# 7b. While holding pvt->lock, remember whether this device is
# a fully initialized Baiwang/QDC507.
# ------------------------------------------------------------
old = '''            int t;
            int is_cmd_timeout = 1;
            if (at_queue_timeout(pvt, &t)) {
                is_cmd_timeout = 0;
            }

            ast_mutex_unlock(&pvt->lock);
'''

new = '''            const int qdc507_poll =
                pvt->initialized &&
                !strncmp(pvt->manufacturer, "Baiwang", 7);

            int t;
            int is_cmd_timeout = 1;
            if (at_queue_timeout(pvt, &t)) {
                is_cmd_timeout = 0;
            }

            ast_mutex_unlock(&pvt->lock);
'''

if old not in s:
    raise RuntimeError(
        "QDC507 poller: cannot find queue-timeout block"
    )

s = s.replace(old, new, 1)


# ------------------------------------------------------------
# 7c. When there is NO outstanding AT command:
#
# Normal devices: keep upstream 10-second ping behavior.
# QDC507: wake every 1000 ms and enqueue one CLCC.
#
# Match the exact idle/no-command branch semantically while
# allowing whitespace differences in upstream source.
# ------------------------------------------------------------
import re

pattern = re.compile(
    r'''
    (?P<indent>[ \t]*)\}\s*else\s*\{\s*
    t\s*=\s*RESPONSE_READ_TIMEOUT\s*;\s*
    if\s*\(\s*!at_wait\s*\(\s*fd\s*,\s*&t\s*\)\s*\)\s*\{\s*
    if\s*\(\s*check_taskprocessor\s*\(\s*tps\s*,\s*dev\s*\)\s*\)\s*\{\s*
    if\s*\(\s*ast_taskprocessor_push\s*\(\s*tps\s*,\s*restart_monitor_taskproc\s*,\s*pvt\s*\)\s*\)\s*\{\s*
    ast_debug\s*\(\s*5\s*,\s*"\[%s\]\s*Unable\ to\ restart\ monitor\ thread\\n"\s*,\s*dev\s*\)\s*;\s*
    \}\s*
    \}\s*
    if\s*\(\s*ast_taskprocessor_push\s*\(\s*tps\s*,\s*at_enqueue_ping_taskproc\s*,\s*pvt\s*\)\s*\)\s*\{\s*
    ast_debug\s*\(\s*5\s*,\s*"\[%s\]\s*Unable\ to\ handle\ timeout\\n"\s*,\s*dev\s*\)\s*;\s*
    \}\s*
    continue\s*;\s*
    \}\s*
    \}
    ''',
    re.VERBOSE
)

matches = list(pattern.finditer(s))
if len(matches) != 1:
    raise RuntimeError(
        f"QDC507 poller: expected exactly one idle wait block, found {len(matches)}"
    )

m = matches[0]
i = m.group("indent")

replacement = f'''{i}}} else {{
{i}    t = qdc507_poll ? 1000 : RESPONSE_READ_TIMEOUT;

{i}    if (!at_wait(fd, &t)) {{
{i}        if (check_taskprocessor(tps, dev)) {{
{i}            if (ast_taskprocessor_push(tps, restart_monitor_taskproc, pvt)) {{
{i}                ast_debug(5, "[%s] Unable to restart monitor thread\\\\n", dev);
{i}            }}
{i}        }}

{i}        if (qdc507_poll) {{
{i}            if (ast_taskprocessor_push(tps,
{i}                                       qdc507_clcc_poll_taskproc,
{i}                                       pvt)) {{
{i}                ast_debug(5,
{i}                          "[%s][QDC507] Unable to poll CLCC\\\\n",
{i}                          dev);
{i}            }}
{i}        }} else {{
{i}            if (ast_taskprocessor_push(tps,
{i}                                       at_enqueue_ping_taskproc,
{i}                                       pvt)) {{
{i}                ast_debug(5,
{i}                          "[%s] Unable to handle timeout\\\\n",
{i}                          dev);
{i}            }}
{i}        }}

{i}        continue;
{i}    }}
{i}}}'''

s = pattern.sub(lambda _: replacement, s, count=1)

print("QDC507: built-in 1-second CLCC poller installed")
print("QDC507 CLCC POLLER PATCH APPLIED SUCCESSFULLY")

p.write_text(s)

print("QDC507: built-in 1-second CLCC poller installed")
print("QDC507 CLCC POLLER PATCH APPLIED SUCCESSFULLY")


# QDC507 unlinked-UAC playback XRUN fix
p = Path('/usr/src/asterisk-chan-quectel/src/channel.c')
s = p.read_text()
old = """        case SND_PCM_STATE_XRUN: {
            res = snd_pcm_prepare(pvt->icard);
            if (res) {
                ast_log(LOG_ERROR, "[%s][ALSA][CAPTURE] Prepare failed - err:'%s'\\n", PVT_ID(pvt), snd_strerror(res));
                goto w_finish;
            }
        }
"""
new = """        case SND_PCM_STATE_XRUN: {
            res = snd_pcm_prepare(pvt->ocard);
            if (res) {
                ast_log(LOG_ERROR, "[%s][ALSA][PLAYBACK] XRUN prepare failed - err:'%s'\\n", PVT_ID(pvt), snd_strerror(res));
                goto w_finish;
            }
        }
"""
if old not in s:
    raise RuntimeError('QDC507 playback XRUN block not found')
s = s.replace(old, new, 1)
p.write_text(s)
print('QDC507: unlinked playback XRUN recovery fixed')


# ============================================================
# 8. Fix pvt_disconnect use-after-free
#
# pvt_disconnect() traverses pvt->chans, but
# cpvt_change_state(... RELEASED ...) calls cpvt_free(),
# which removes and frees the current list node.
#
# Save entry.next BEFORE releasing the current cpvt.
# ============================================================
p = Path("/usr/src/asterisk-chan-quectel/src/chan_quectel.c")
s = p.read_text()

old = '''    if (!PVT_NO_CHANS(pvt)) {
        struct cpvt* cpvt;
        AST_LIST_TRAVERSE(&(pvt->chans), cpvt, entry) {
            at_hangup_immediately(cpvt, AST_CAUSE_NORMAL_UNSPECIFIED);
            CPVT_RESET_FLAG(cpvt, CALL_FLAG_NEED_HANGUP);
            cpvt_change_state(cpvt, CALL_STATE_RELEASED, AST_CAUSE_NORMAL_UNSPECIFIED);
        }
    }
'''

new = '''    if (!PVT_NO_CHANS(pvt)) {
        struct cpvt* cpvt = AST_LIST_FIRST(&pvt->chans);

        while (cpvt) {
            /*
             * cpvt_change_state(RELEASED) may remove and free cpvt,
             * so entry.next must be saved before releasing it.
             */
            struct cpvt* const next = AST_LIST_NEXT(cpvt, entry);

            at_hangup_immediately(cpvt, AST_CAUSE_NORMAL_UNSPECIFIED);
            CPVT_RESET_FLAG(cpvt, CALL_FLAG_NEED_HANGUP);
            cpvt_change_state(cpvt, CALL_STATE_RELEASED,
                              AST_CAUSE_NORMAL_UNSPECIFIED);

            cpvt = next;
        }
    }
'''

if old not in s:
    raise RuntimeError("QDC507 UAF fix: pvt_disconnect block not found")

s = s.replace(old, new, 1)
p.write_text(s)

print("QDC507: pvt_disconnect UAF traversal fixed")


# ============================================================
# 9. Fix QDC507 CLCC poll interval across lock contention
#
# The original 1-second patch only changes the idle branch after
# monitor_thread acquires pvt->lock.
#
# If trylock fails, upstream still waits RESPONSE_READ_TIMEOUT
# (10 seconds), so CLCC polling can stall for many seconds,
# especially immediately after AT response processing.
#
# Cache the QDC507 state only while holding pvt->lock and use
# that cached flag in the lock-contention branch.
# ============================================================
p = Path("/usr/src/asterisk-chan-quectel/src/monitor_thread.c")
s = p.read_text()

old = '''    int read_result = 0;
    while (1) {
'''

new = '''    int read_result = 0;

    /*
     * Updated only while pvt->lock is held.
     * The monitor can safely use the cached value when trylock fails.
     */
    int qdc507_poll_enabled = 0;

    while (1) {
'''

if old not in s:
    raise RuntimeError("QDC507 poll fix: read_result/while block not found")

s = s.replace(old, new, 1)

old = '''        if (ast_mutex_trylock(&pvt->lock)) {  // pvt unlocked
            int t = RESPONSE_READ_TIMEOUT;
            if (!at_wait(fd, &t)) {
                if (ast_taskprocessor_push(tps, at_enqueue_ping_taskproc, pvt)) {
                    ast_debug(5, "[%s] Unable to handle timeout\\n", dev);
                }
                continue;
            }
        } else {  // pvt locked
'''

new = '''        if (ast_mutex_trylock(&pvt->lock)) {  // pvt lock not acquired
            /*
             * QDC507 must not fall back to the upstream 10-second
             * wait merely because the response taskprocessor owns
             * pvt->lock briefly.
             */
            int t = qdc507_poll_enabled ? 1000 : RESPONSE_READ_TIMEOUT;

            if (!at_wait(fd, &t)) {
                if (qdc507_poll_enabled) {
                    if (ast_taskprocessor_push(tps,
                                               qdc507_clcc_poll_taskproc,
                                               pvt)) {
                        ast_debug(5,
                                  "[%s][QDC507] Unable to poll CLCC while lock busy\\n",
                                  dev);
                    }
                } else {
                    if (ast_taskprocessor_push(tps,
                                               at_enqueue_ping_taskproc,
                                               pvt)) {
                        ast_debug(5,
                                  "[%s] Unable to handle timeout\\n",
                                  dev);
                    }
                }
                continue;
            }
        } else {  // pvt locked
'''

if old not in s:
    raise RuntimeError("QDC507 poll fix: trylock branch not found")

s = s.replace(old, new, 1)

old = '''            const int qdc507_poll =
                pvt->initialized &&
                !strncmp(pvt->manufacturer, "Baiwang", 7);
'''

new = '''            qdc507_poll_enabled =
                pvt->initialized &&
                !strncmp(pvt->manufacturer, "Baiwang", 7);

            const int qdc507_poll = qdc507_poll_enabled;
'''

if old not in s:
    raise RuntimeError("QDC507 poll fix: qdc507_poll block not found")

s = s.replace(old, new, 1)

p.write_text(s)

print("QDC507: CLCC lock-contention polling fixed")

# QDC507 immediate post-hangup CLCC reconciliation

# QDC507 immediate post-hangup CLCC reconciliation
# CHUP returning OK means the modem accepted the command, but prior live
# traces showed the modem's call list can lag until a later CLCC query.
# Reuse chan_quectel's serialized AT queue and existing CLCC parser/finalizer:
# do not create another serial reader or an external polling process.
p = Path('/usr/src/asterisk-chan-quectel/src/at_response.c')
s = p.read_text()
old = """        case CMD_AT_CHUP:
        case CMD_AT_QHUP:
        case CMD_AT_CHLD_1x:
            CPVT_RESET_FLAG(task->cpvt, CALL_FLAG_NEED_HANGUP);
            at_ok_response_dbg(1, pvt, ecmd, \"Successful hangup for call idx:%d\", task->cpvt->call_idx);
            break;
"""
new = """        case CMD_AT_CHUP:
        case CMD_AT_QHUP:
        case CMD_AT_CHLD_1x: {
            const int hungup_idx = task->cpvt ? task->cpvt->call_idx : -1;
            CPVT_RESET_FLAG(task->cpvt, CALL_FLAG_NEED_HANGUP);
            at_ok_response_dbg(1, pvt, ecmd, \"Successful hangup for call idx:%d\", hungup_idx);

            /*
             * Immediate reconciliation after successful CHUP. The CLCC
             * command is inserted into the existing AT queue while this
             * serialized response task owns pvt->lock. Queue removal of
             * CHUP happens just after this handler; monitor_thread then
             * transmits CLCC. The existing trailing-OK CLCC finalizer
             * releases only call indexes absent from the completed snapshot.
             */
            if (!pvt->is_simcom && pvt->initialized &&
                !strncmp(pvt->manufacturer, \"Baiwang\", 7)) {
                ast_verb(2,
                         \"[%s][QDC507] Hangup accepted for idx:%d; request immediate CLCC reconciliation\\n\",
                         PVT_ID(pvt), hungup_idx);
                if (at_enqueue_clcc(&pvt->sys_chan)) {
                    ast_log(LOG_WARNING,
                            \"[%s][QDC507] Unable to enqueue post-hangup CLCC\\n\",
                            PVT_ID(pvt));
                }
            }
            break;
        }
"""
if old not in s:
 raise RuntimeError('could not locate exact CHUP/QHUP response case')
s=s.replace(old,new,1)
p.write_text(s)
print('QDC507: immediate post-hangup CLCC reconciliation added')
from pathlib import Path
import re
ROOT = Path('/usr/src/asterisk-chan-quectel')

def one(src, old, new, label):
    n=src.count(old)
    if n != 1:
        raise RuntimeError(f'{label}: expected one anchor, got {n}')
    return src.replace(old,new,1)

def replace_func(src, signature, new_body):
    a=src.find(signature)
    if a<0: raise RuntimeError('function missing: '+signature)
    brace=src.find('{',a); depth=0
    for i in range(brace,len(src)):
        if src[i]=='{': depth+=1
        elif src[i]=='}':
            depth-=1
            if depth==0: return src[:a]+new_body+src[i+1:]
    raise RuntimeError('function braces unbalanced: '+signature)

def func_bounds(src, signature):
    a=src.find(signature)
    if a<0: raise RuntimeError('function missing: '+signature)
    brace=src.find('{',a); depth=0
    for i in range(brace,len(src)):
        if src[i]=='{': depth+=1
        elif src[i]=='}':
            depth-=1
            if depth==0: return a,i+1
    raise RuntimeError('unbalanced function: '+signature)

# Per-call flags, never infer release from an ordinary poll alone.
p=ROOT/'src/cpvt.h'; s=p.read_text()
if 'CALL_FLAG_QDC507_HANGUP_PENDING' not in s:
    a='    CALL_FLAG_INTERNAL_REQUEST = 2048  /*!< internal request */'
    b=a+',\n    CALL_FLAG_QDC507_HANGUP_PENDING = 4096 /*!< local hangup awaits CLCC */,\n    CALL_FLAG_QDC507_CLCC_TRACKED = 8192 /*!< observed by modem CLCC */'
    s=one(s,a,b,'cpvt flags')
if 'qdc507_clcc_missing' not in s:
    a='    call_state_t state; /*!< see also call_state_t */'
    b=a+'\n    unsigned char qdc507_clcc_missing; /*!< complete snapshots missing */'
    s=one(s,a,b,'cpvt missing counter')
p.write_text(s)

# Snapshot guard.
p=ROOT/'src/chan_quectel.h'; s=p.read_text()
if 'qdc507_clcc_snapshot_invalid' not in s:
    a='    uint32_t qdc507_clcc_seen; /*!< QDC507 calls seen in current CLCC generation */'
    s=one(s,a,a+'\n    unsigned int qdc507_clcc_snapshot_invalid:1; /*!< malformed snapshot guard */','pvt snapshot guard')
p.write_text(s)

# Mark only after Asterisk successfully queues a local hangup command.
p=ROOT/'src/channel.c'; s=p.read_text()
if 'CALL_FLAG_QDC507_HANGUP_PENDING' not in s:
    a='''            } else {
                CPVT_RESET_FLAG(cpvt, CALL_FLAG_NEED_HANGUP);
            }
        }

        cpvt_call_disactivate(cpvt);'''
    b='''            } else {
                CPVT_RESET_FLAG(cpvt, CALL_FLAG_NEED_HANGUP);
                if (!pvt->is_simcom && pvt->initialized &&
                    !strncmp(pvt->manufacturer, "Baiwang", 7) &&
                    cpvt->state != CALL_STATE_INIT &&
                    cpvt->call_idx >= MIN_CALL_IDX && cpvt->call_idx <= MAX_CALL_IDX) {
                    CPVT_SET_FLAG(cpvt, CALL_FLAG_QDC507_HANGUP_PENDING);
                    CPVT_RESET_FLAG(cpvt, CALL_FLAG_ALIVE);
                    cpvt->qdc507_clcc_missing = 0;
                }
            }
        }

        cpvt_call_disactivate(cpvt);'''
    s=one(s,a,b,'channel hangup marker')
p.write_text(s)

# The pinned base patch already de-duplicates CLCC in its at_command.c path.

# Make malformed/unsupported voice rows invalidate the snapshot. Base patch
# already resets CALL_FLAG_ALIVE before parsing and sets it in handle_clcc.
p=ROOT/'src/at_response.c'; s=p.read_text()
start,end=func_bounds(s,'static int at_response_clcc(')
block=s[start:end]
if 'qdc507_clcc_snapshot_invalid = 0u' not in block:
    a='''    if (!pvt->initialized) {
        return 0;
    }

    struct cpvt* cpvt;'''
    b='''    if (!pvt->initialized) {
        return 0;
    }
    pvt->qdc507_clcc_snapshot_invalid = 0u;

    struct cpvt* cpvt;'''
    block=one(block,a,b,'CLCC snapshot begin')
    a='''            ast_log(LOG_ERROR, "[%s] CLCC - can't parse line '%s'\\n", PVT_ID(pvt), ast_str_buffer(line));
            continue;'''
    b='''            pvt->qdc507_clcc_snapshot_invalid = 1u;
            ast_log(LOG_ERROR, "[%s] CLCC - can't parse line '%s'\\n", PVT_ID(pvt), ast_str_buffer(line));
            continue;'''
    block=one(block,a,b,'CLCC parse error')
    a='''        if (mode > CALL_STATE_WAITING) {
            ast_debug(4, "[%s] CLCC - invalid call state, idx:%u dir:%u state:%u nubmer:%s\\n", PVT_ID(pvt), call_idx, dir, state, number);
            continue;
        }'''
    b='''        if (mode > CALL_STATE_WAITING || call_idx > MAX_CALL_IDX) {
            pvt->qdc507_clcc_snapshot_invalid = 1u;
            ast_debug(4, "[%s] CLCC invalid row idx:%u state:%u\\n", PVT_ID(pvt), call_idx, state);
            continue;
        }'''
    block=one(block,a,b,'CLCC row validation')
    # A valid response is authoritative only for a call actually mapped to
    # chan_quectel cpvt; pending local hangup is tracked separately.
    a='''        handle_clcc(pvt, call_idx, dir, state, mode, mpty ? TRIBOOL_TRUE : TRIBOOL_FALSE, number, type);'''
    b=a+'\n        struct cpvt* observed=pvt_channel_find_by_call_idx(pvt,(int)call_idx);\n        if (observed) CPVT_SET_FLAG(observed,CALL_FLAG_QDC507_CLCC_TRACKED | CALL_FLAG_ALIVE);'
    block=one(block,a,b,'CLCC mapped call tracking')
    s=s[:start]+block+s[end:]

# Replace unconditional finalizer: local CHUP absence = release after one
# complete snapshot; other already-tracked calls need two complete absences.
finalizer='''static void qdc507_clcc_finalize(struct pvt* const pvt)
{
    if (!pvt->initialized || strncmp(pvt->manufacturer, "Baiwang", 7)) return;
    if (pvt->qdc507_clcc_snapshot_invalid) {
        ast_log(LOG_WARNING, "[%s][QDC507] Invalid CLCC snapshot; preserve channels\\n", PVT_ID(pvt));
        pvt->qdc507_clcc_snapshot_invalid = 0u;
        pvt->qdc507_clcc_seen = 0u;
        return;
    }
    for (;;) {
        struct cpvt* stale = NULL;
        struct cpvt* cpvt;
        AST_LIST_TRAVERSE(&pvt->chans, cpvt, entry) {
            if (cpvt->state == CALL_STATE_INIT) continue;
            int pending=CPVT_TEST_FLAG(cpvt,CALL_FLAG_QDC507_HANGUP_PENDING);
            int tracked=CPVT_TEST_FLAG(cpvt,CALL_FLAG_QDC507_CLCC_TRACKED);
            if (!pending && !tracked) continue;
            if (CPVT_TEST_FLAG(cpvt,CALL_FLAG_ALIVE)) {
                cpvt->qdc507_clcc_missing=0;
                continue;
            }
            if (pending) { stale=cpvt; break; }
            if (cpvt->qdc507_clcc_missing < 2) cpvt->qdc507_clcc_missing++;
            if (cpvt->qdc507_clcc_missing >= 2) { stale=cpvt; break; }
        }
        if (!stale) break;
        int idx=stale->call_idx;
        CPVT_RESET_FLAG(stale,CALL_FLAG_QDC507_HANGUP_PENDING | CALL_FLAG_QDC507_CLCC_TRACKED | CALL_FLAG_ALIVE);
        ast_verb(2,"[%s][QDC507] complete CLCC confirms idx:%d absent -> RELEASED\\n",PVT_ID(pvt),idx);
        cpvt_change_state(stale,CALL_STATE_RELEASED,AST_CAUSE_NORMAL_CLEARING);
    }
    pvt->qdc507_clcc_snapshot_invalid=0u;
    pvt->qdc507_clcc_seen=0u;
}'''
s=replace_func(s,'static void qdc507_clcc_finalize(',finalizer)
p.write_text(s)

# Base pinned source treats CLCC ERROR/TIMEOUT as fatal. Preserve the device
# and pending call state for the next scheduled poll.
p=ROOT/'src/at_response.c'; s=p.read_text()
old='''        case CMD_AT:
        case CMD_AT_Z:
        case CMD_AT_E:
        case CMD_AT_CLCC:
            at_err_response_err(pvt, ecmd, NULL);
            /* mean disconnected from device */
            goto e_return;'''
new='''        case CMD_AT:
        case CMD_AT_Z:
        case CMD_AT_E:
            at_err_response_err(pvt, ecmd, NULL);
            /* mean disconnected from device */
            goto e_return;
        case CMD_AT_CLCC:
            at_err_response_wrn(pvt, ecmd, "CLCC failed; preserve pending calls");
            pvt->qdc507_clcc_snapshot_invalid=0u;
            pvt->qdc507_clcc_seen=0u;
            break;'''
s=one(s,old,new,'CLCC nonfatal error')
# Narrow the existing base-patch CHUP callback to the marked local hangup.
start=s.find('static int at_response_ok('); end=s.find('static void __attribute__',start)
if start<0 or end<0: raise RuntimeError('at_response_ok bounds')
ok=s[start:end]
a='''            if (!pvt->is_simcom && pvt->initialized &&
                !strncmp(pvt->manufacturer, "Baiwang", 7)) {'''
b='''            if (task->cpvt && CPVT_TEST_FLAG(task->cpvt, CALL_FLAG_QDC507_HANGUP_PENDING) &&
                !pvt->is_simcom && pvt->initialized && !strncmp(pvt->manufacturer, "Baiwang", 7)) {'''
ok=one(ok,a,b,'CHUP pending condition')
s=s[:start]+ok+s[end:]
p.write_text(s)

# Monitor-owned scheduler: no recurring taskprocessor callbacks with pvt*.
p=ROOT/'src/monitor_thread.c'; s=p.read_text()
helper=re.compile(r'\nstatic void qdc507_clcc_poll\(struct pvt\* const pvt\)\n\{.*?\n\}\n\nstatic int qdc507_clcc_poll_taskproc\(void\* tpdata\)\n\{.*?\n\}\n',re.S)
s,n=helper.subn('\n',s,count=1)
if n!=1: raise RuntimeError(f'old CLCC poll taskproc count {n}')
a='''    int read_result = 0;

    /*
     * Updated only while pvt->lock is held.
     * The monitor can safely use the cached value when trylock fails.
     */
    int qdc507_poll_enabled = 0;

    while (1) {'''
b='''    int read_result = 0;
    int qdc507_poll_enabled = 0;
    int qdc507_clcc_due = 0;

    while (1) {'''
s=one(s,a,b,'monitor due bit')
# Lock contention: local due bit, never enqueue a raw pvt callback.
# Match the structural lock-busy block, not formatting of debug messages.
start=s.find('        if (ast_mutex_trylock(&pvt->lock)) {  // pvt lock not acquired')
end=s.find('        } else {  // pvt locked',start)
if start<0 or end<0: raise RuntimeError('monitor trylock branch missing')
end+=len('        } else {  // pvt locked\n')
newblock='''        if (ast_mutex_trylock(&pvt->lock)) {  // pvt lock not acquired
            int t=qdc507_poll_enabled?1000:RESPONSE_READ_TIMEOUT;
            if(!at_wait(fd,&t)) {
                if(qdc507_poll_enabled) qdc507_clcc_due=1;
                else if(ast_taskprocessor_push(tps,at_enqueue_ping_taskproc,pvt))
                    ast_debug(5,"[%s] Unable to handle timeout\\n",dev);
                continue;
            }
        } else {  // pvt locked
'''
s=s[:start]+newblock+s[end:]
a='''            const int qdc507_poll = qdc507_poll_enabled;

            int t;'''
b='''            const int qdc507_poll=pvt->initialized && !strncmp(pvt->manufacturer,"Baiwang",7);
            qdc507_poll_enabled=qdc507_poll;
            if(qdc507_clcc_due && qdc507_poll && AST_LIST_EMPTY(&pvt->at_queue)) {
                qdc507_clcc_due=0;
                if(at_enqueue_clcc(&pvt->sys_chan))
                    ast_log(LOG_WARNING,"[%s][QDC507] monitor CLCC enqueue failed\\n",PVT_ID(pvt));
                else ast_debug(3,"[%s][QDC507] monitor-owned CLCC queued\\n",PVT_ID(pvt));
            } else if(qdc507_clcc_due && !qdc507_poll) qdc507_clcc_due=0;
            int t;'''
s=one(s,a,b,'monitor due dispatch')
# Idle timeout branch queues only the local due bit. Keep standard non-QDC ping.
# Structure-bound idle arm replacement avoids nested Python/C backslash drift.
start=s.find('            } else {\n                t = qdc507_poll ? 1000 : RESPONSE_READ_TIMEOUT;', s.find('static void monitor_threadproc_pvt'))
end=s.find('\n        /* FIXME: access to device not locked */', start)
if start<0 or end<0: raise RuntimeError('monitor idle branch bounds missing')
section=s[start:end]
close=section.rfind('\n            }\n        }')
if close<0: raise RuntimeError('monitor idle branch close missing')
newbranch="""            } else {
                t = qdc507_poll ? 1000 : RESPONSE_READ_TIMEOUT;
                if (!at_wait(fd, &t)) {
                    if (qdc507_poll) qdc507_clcc_due=1;
                    else if (ast_taskprocessor_push(tps, at_enqueue_ping_taskproc, pvt))
                        ast_debug(5, \"[%s] Unable to handle timeout\\n\", dev);
                    continue;
                }
            }"""
section=newbranch+section[close+len('\n            }'):]
s=s[:start]+section+s[end:]
p.write_text(s)
print('QDC507_SAFE_CLCC_PATCH_APPLIED')
from pathlib import Path
R=Path('/usr/src/asterisk-chan-quectel/src')
def swap(p,a,b,label):
 s=p.read_text()
 if s.count(a)!=1: raise RuntimeError(f'{label}: {s.count(a)} matches')
 p.write_text(s.replace(a,b,1))
# Start a new generation at command enqueue, not at each +CLCC row.
p=R/'at_command.c';s=p.read_text();start=s.index('int at_enqueue_clcc(');end=s.index('\n}',start)+2;old=s[start:end]
new='''int at_enqueue_clcc(struct cpvt* cpvt)
{
    DECLARE_AT_CMD(clcc, "+CLCC");
    static const at_queue_cmd_t cmd = ATQ_CMD_DECLARE_ST(CMD_AT_CLCC, clcc);
    struct pvt* pvt = cpvt->pvt;
    struct at_queue_task* queued;
    AST_LIST_TRAVERSE(&pvt->at_queue, queued, entry) {
        const at_queue_cmd_t* current = at_queue_task_cmd(queued);
        if (current && current->cmd == CMD_AT_CLCC) return 0;
    }
    pvt->qdc507_clcc_seen = 0u;
    pvt->qdc507_clcc_snapshot_invalid = 0u;
    /* A fresh snapshot starts here. Later +CLCC lines set ALIVE via
     * handle_clcc; do not clear it once per response line. */
    struct cpvt* channel;
    AST_LIST_TRAVERSE(&pvt->chans, channel, entry) {
        CPVT_RESET_FLAG(channel, CALL_FLAG_ALIVE);
    }
    if (at_queue_insert_const(cpvt, &cmd, 1u, 1)) {
        chan_quectel_err = E_QUEUE;
        return -1;
    }
    return 0;
}'''
if 'qdc507_clcc_snapshot_invalid' in old: raise RuntimeError('already patched enqueue')
p.write_text(s[:start]+new+s[end:])
# A single +CLCC line is not the complete snapshot.
p=R/'at_response.c';s=p.read_text();a='''    pvt->qdc507_clcc_snapshot_invalid = 0u;

    struct cpvt* cpvt;
    AST_LIST_TRAVERSE(&pvt->chans, cpvt, entry) {
        CPVT_RESET_FLAG(cpvt, CALL_FLAG_ALIVE);
    }
'''
if s.count(a)!=1: raise RuntimeError('per-line reset anchor mismatch')
s=s.replace(a,'',1)
start=s.index('static int at_response_clcc(');end=s.index('\n}',start)+2;f=s[start:end]
a='''        handle_clcc(pvt, call_idx, dir, state, mode, mpty ? TRIBOOL_TRUE : TRIBOOL_FALSE, number, type);
        struct cpvt* observed=pvt_channel_find_by_call_idx(pvt,(int)call_idx);
        if (observed) CPVT_SET_FLAG(observed,CALL_FLAG_QDC507_CLCC_TRACKED | CALL_FLAG_ALIVE);'''
b='''        handle_clcc(pvt, call_idx, dir, state, mode, mpty ? TRIBOOL_TRUE : TRIBOOL_FALSE, number, type);
        struct cpvt* observed=pvt_channel_find_by_call_idx(pvt,(int)call_idx);
        if (observed) CPVT_SET_FLAG(observed,CALL_FLAG_QDC507_CLCC_TRACKED);'''
if f.count(a)!=1: raise RuntimeError('row seen anchor mismatch')
s=s[:start]+f.replace(a,b,1)+s[end:]
# Existing parser already marks the index once; preserve that single mark.
# Deliberately do not force-release an unrelated call from an unvalidated row.
p.write_text(s)
print('CLCC snapshot per-transaction reset and queue dedup applied')
from pathlib import Path
P=Path('/usr/src/asterisk-chan-quectel/src/monitor_thread.c')
s=P.read_text()
a='''                } else if (!at_wait(fd, &t)) {
                    if (ast_taskprocessor_push(tps, cmd_timeout_taskproc, pvt)) {
                        ast_debug(5, "[%s] Unable to handle timeout\\n", dev);
                    }
                    continue;
                }
            } else {
                t = qdc507_poll ? 1000 : RESPONSE_READ_TIMEOUT;'''
b='''                } else {
                    /* Responses are consumed on the serializer, not here. A
                     * successful CLCC can leave this thread sleeping until
                     * the old command's 5-second deadline. Recheck QDC507's
                     * queue at most every second, without generating a false
                     * command timeout on an ordinary 1-second wake-up. */
                    const int sliced = qdc507_poll && t > 1000;
                    if (sliced) t = 1000;
                    if (!at_wait(fd, &t)) {
                        if (!sliced) {
                            if (ast_taskprocessor_push(tps, cmd_timeout_taskproc, pvt)) {
                                ast_debug(5, "[%s] Unable to handle timeout\\n", dev);
                            }
                        }
                        continue;
                    }
                }
            } else {
                t = qdc507_poll ? 1000 : RESPONSE_READ_TIMEOUT;'''
if s.count(a)!=1: raise RuntimeError(f'CLCC timed wait anchor count={s.count(a)}')
s=s.replace(a,b,1)
P.write_text(s)
print('QDC507_CLCC_COMMAND_WAIT_RECHECK_1000MS_APPLIED')
