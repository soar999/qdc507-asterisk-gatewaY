/*
    monitor_thread.c
*/

#include <signal.h>  /* SIGURG */
#include <termios.h> /* struct termios tcgetattr() tcsetattr()  */

#include "ast_config.h"

#include <asterisk/lock.h>
#include <asterisk/strings.h>
#include <asterisk/taskprocessor.h>
#include <asterisk/threadpool.h>

#include "monitor_thread.h"

#include "at_queue.h"
#include "at_read.h"
#include "chan_quectel.h"
#include "channel.h"
#include "helpers.h"
#include "smsdb.h"
#include "tty.h"

static const int TASKPROCESSOR_HIGH_WATER = 400;

static struct ast_taskprocessor* threadpool_serializer(struct ast_threadpool* pool, const char* const dev)
{
    char taskprocessor_name[AST_TASKPROCESSOR_MAX_NAME + 1];
    ast_taskprocessor_build_name(taskprocessor_name, sizeof(taskprocessor_name), "chan-quectel/%s", dev);
    struct ast_taskprocessor* const res = ast_threadpool_serializer(taskprocessor_name, pool);
    if (res) {
        ast_taskprocessor_alert_set_levels(res, -1, TASKPROCESSOR_HIGH_WATER);
    }
    return res;
}

static int check_taskprocessor(struct ast_taskprocessor* tps, const char* dev)
{
    const long size     = ast_taskprocessor_size(tps);
    const int suspended = ast_taskprocessor_is_suspended(tps);
    if (size || suspended) {
        ast_log(LOG_WARNING, "[%s] Taskprocessor - size:%ld suspended:%d\n", dev, size, suspended);
    }
    return size >= TASKPROCESSOR_HIGH_WATER;
}

static void handle_expired_reports(struct pvt* pvt)
{
    static const size_t SMSDB_DEF_LEN = 64;

    RAII_VAR(struct ast_str*, dst, ast_str_create(SMSDB_DEF_LEN), ast_free);
    RAII_VAR(struct ast_str*, msg, ast_str_create(SMSDB_DEF_LEN), ast_free);

    int uid;

    if (smsdb_outgoing_purge_one(&uid, &dst, &msg)) {
        return;
    }

    ast_verb(3, "[%s][SMS:%d %s] Expired\n", PVT_ID(pvt), uid, ast_str_buffer(dst));

    RAII_VAR(struct ast_json*, report, ast_json_object_create(), ast_json_unref);
    ast_json_object_set(report, "info", ast_json_string_create("Message expired"));
    ast_json_object_set(report, "uid", ast_json_integer_create(uid));
    ast_json_object_set(report, "expired", ast_json_integer_create(1));
    AST_JSON_OBJECT_SET(report, msg);
    channel_start_local_report(pvt, "sms", LOCAL_REPORT_DIRECTION_OUTGOING, ast_str_buffer(dst), NULL, NULL, 0, report);
}

static int handle_expired_reports_taskproc(void* tpdata) { return PVT_TASKPROC_TRYLOCK_AND_EXECUTE(tpdata, handle_expired_reports); }

static void restart_monitor(struct pvt* pvt) { pvt->terminate_monitor = 1; }

static int restart_monitor_taskproc(void* tpdata) { return PVT_TASKPROC_TRYLOCK_AND_EXECUTE(tpdata, restart_monitor); }

/* Called only with pvt->lock held; never retain a cpvt across the wait. */
static int qdc507_poll_wait_ms(struct pvt* const pvt)
{
    /* Only the evidenced model gets the new fast phase; legacy polling stays. */
    if (!pvt->initialized || pvt->is_simcom ||
        strcmp(pvt->manufacturer, "Baiwang") || strcmp(pvt->model, "QDC507")) return 1000;
    struct cpvt* cpvt;
    AST_LIST_TRAVERSE(&pvt->chans, cpvt, entry) {
        if (CPVT_DIR_OUTGOING(cpvt) &&
            !CPVT_TEST_FLAG(cpvt, CALL_FLAG_QDC507_HANGUP_PENDING) &&
            (cpvt->state == CALL_STATE_DIALING ||
             cpvt->state == CALL_STATE_ALERTING)) {
            return 500;
        }
    }
    /* No reliable answer-in-progress state: incoming ringing stays slow. */
    return 1000;
}

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


static void cmd_timeout(struct pvt* const pvt)
{
    const struct at_queue_cmd* const ecmd = at_queue_head_cmd(pvt);
    if (!ecmd || ecmd->length) {
        return;
    }

    /* Recheck the current head: a queued callback may outlive its command.
     * Compare the absolute timeval, not rounded milliseconds (1us is future). */
    if (pvt->initialized && !strncmp(pvt->manufacturer, "Baiwang", 7)) {
        const struct timeval now = ast_tvnow();
        if (ecmd->timeout.tv_sec > now.tv_sec ||
            (ecmd->timeout.tv_sec == now.tv_sec && ecmd->timeout.tv_usec > now.tv_usec)) {
            return;
        }
    }

    /* at_response may remove and free the entire task, including ecmd.
     * Snapshot this command's policy for ALL devices before that call. */
    const unsigned ignore_timeout = ecmd->flags & ATQ_CMD_FLAG_IGNORE;
    if (at_response(pvt, &pvt->empty_str, RES_TIMEOUT)) {
        ast_log(LOG_ERROR, "[%s] Fail to handle response\n", PVT_ID(pvt));
        pvt->terminate_monitor = 1;
        return;
    }

    if (ignore_timeout) {
        return;
    }

    pvt->terminate_monitor = 1;
}

static int cmd_timeout_taskproc(void* tpdata) { return PVT_TASKPROC_TRYLOCK_AND_EXECUTE(tpdata, cmd_timeout); }

static int reopen_audio_port(struct pvt* pvt)
{
    tty_close_lck(CONF_UNIQ(pvt, audio_tty), pvt->audio_fd, 0, 0);
    pvt->audio_fd = tty_open(CONF_UNIQ(pvt, audio_tty), pvt->is_simcom);

    if (!PVT_NO_CHANS(pvt)) {
        struct cpvt* cpvt;
        AST_LIST_TRAVERSE(&(pvt->chans), cpvt, entry) {
            ast_channel_set_fd(cpvt->channel, 0, pvt->audio_fd);
        }
    }

    return (pvt->audio_fd > 0);
}

static void pcm_show_playback_state(struct pvt* const pvt) { pcm_show_state(4, "PLAYBACK", PVT_ID(pvt), pvt->ocard); }

static void pcm_show_capture_state(struct pvt* const pvt) { pcm_show_state(4, "CAPTURE", PVT_ID(pvt), pvt->icard); }

static int pcm_show_playback_state_taskproc(void* tpdata) { return PVT_TASKPROC_TRYLOCK_AND_EXECUTE(tpdata, pcm_show_playback_state); }

static int pcm_show_capture_state_taskproc(void* tpdata) { return PVT_TASKPROC_TRYLOCK_AND_EXECUTE(tpdata, pcm_show_capture_state); }

static void push_pcm_state_taskprocs(struct ast_taskprocessor* tps, struct pvt* const pvt)
{
    if (ast_taskprocessor_push(tps, pcm_show_playback_state_taskproc, pvt)) {
        ast_debug(5, "[%s] Unable to show ALSA playback state\n", PVT_ID(pvt));
    }
    if (ast_taskprocessor_push(tps, pcm_show_capture_state_taskproc, pvt)) {
        ast_debug(5, "[%s] Unable to show ALSA capture state\n", PVT_ID(pvt));
    }
}

static int check_dev_status(struct pvt* const pvt, struct ast_taskprocessor* tps)
{
    int err;
    if (tty_status(pvt->data_fd, &err)) {
        ast_log(LOG_ERROR, "[%s][DATA] Lost connection: %s\n", PVT_ID(pvt), strerror(err));
        return -1;
    }

    switch (CONF_UNIQ(pvt, uac)) {
        case TRIBOOL_FALSE:
            if (tty_status(pvt->audio_fd, &err)) {
                if (reopen_audio_port(pvt)) {
                    ast_log(LOG_WARNING, "[%s][AUDIO][TTY] Lost connection: %s\n", PVT_ID(pvt), strerror(err));
                } else {
                    ast_log(LOG_ERROR, "[%s][AUDIO][TTY] Lost connection: %s\n", PVT_ID(pvt), strerror(err));
                    return -1;
                }
            }
            break;

        case TRIBOOL_TRUE:
            push_pcm_state_taskprocs(tps, pvt);
            break;

        case TRIBOOL_NONE:
            push_pcm_state_taskprocs(tps, pvt);

            if (pcm_status(pvt->ocard, pvt->icard)) {
                ast_log(LOG_ERROR, "[%s][AUDIO][ALSA] Lost connection\n", PVT_ID(pvt));
                return -1;
            }
            break;
    }
    return 0;
}

static void monitor_threadproc_pvt(struct pvt* const pvt)
{
    static const size_t RINGBUFFER_SIZE = 2 * 1024;

    static const int RESPONSE_READ_TIMEOUT     = 10000;
    static const int UNHANDLED_COMMAND_TIMEOUT = 500;

    struct ringbuffer rb;
    RAII_VAR(void* const, buf, ast_calloc(1, RINGBUFFER_SIZE), ast_free);
    rb_init(&rb, buf, RINGBUFFER_SIZE);

    /* at_combine_iov() may copy all RINGBUFFER_SIZE bytes and
     * ast_str_truncate() writes one trailing NUL byte. */
    RAII_VAR(struct ast_str* const, result, ast_str_create(RINGBUFFER_SIZE + 1u), ast_free);

    ast_mutex_lock(&pvt->lock);
    RAII_VAR(char* const, dev, ast_strdup(PVT_ID(pvt)), ast_free);

    RAII_VAR(struct ast_taskprocessor*, tps, threadpool_serializer(gpublic->threadpool, dev), ast_taskprocessor_unreference);
    if (!tps) {
        ast_log(LOG_ERROR, "[%s] Error initializing taskprocessor\n", dev);
        goto e_cleanup;
    }

    /* 4 reduce locking time make copy of this readonly fields */
    const int fd = pvt->data_fd;
    at_clean_data(dev, fd, &rb);

    /* schedule initilization  */
    if (at_enqueue_initialization(&pvt->sys_chan)) {
        ast_log(LOG_ERROR, "[%s] Error adding initialization commands to queue\n", dev);
        goto e_cleanup;
    }

    ast_mutex_unlock(&pvt->lock);

    int read_result = 0;

    /*
     * Updated only while pvt->lock is held.
     * The monitor can safely use the cached value when trylock fails.
     */
    int qdc507_poll_enabled = 0;

    while (1) {
        if (ast_taskprocessor_push(tps, handle_expired_reports_taskproc, pvt)) {
            ast_debug(5, "[%s] Unable to handle exprired reports\n", dev);
        }

        if (ast_mutex_trylock(&pvt->lock)) {  // pvt lock not acquired
            /*
             * QDC507 must not fall back to the upstream 10-second
             * wait merely because the response taskprocessor owns
             * pvt->lock briefly. Keep the conservative 1000ms fallback:
             * never read call state unlocked or cache a stale fast phase.
             */
            int t = qdc507_poll_enabled ? 1000 : RESPONSE_READ_TIMEOUT;

            if (!at_wait(fd, &t)) {
                if (qdc507_poll_enabled) {
                    if (ast_taskprocessor_push(tps,
                                               qdc507_clcc_poll_taskproc,
                                               pvt)) {
                        ast_debug(5,
                                  "[%s][QDC507] Unable to poll CLCC while lock busy\n",
                                  dev);
                    }
                } else {
                    if (ast_taskprocessor_push(tps,
                                               at_enqueue_ping_taskproc,
                                               pvt)) {
                        ast_debug(5,
                                  "[%s] Unable to handle timeout\n",
                                  dev);
                    }
                }
                continue;
            }
        } else {  // pvt locked
            if (check_dev_status(pvt, tps)) {
                goto e_cleanup;
            }

            if (pvt->terminate_monitor) {
                ast_log(LOG_NOTICE, "[%s] Stopping by %s request\n", dev, dev_state2str(pvt->desired_state));
                goto e_restart;
            }

            qdc507_poll_enabled =
                pvt->initialized &&
                !strncmp(pvt->manufacturer, "Baiwang", 7);

            const int qdc507_poll = qdc507_poll_enabled;
            const int poll_wait_ms = qdc507_poll ? qdc507_poll_wait_ms(pvt) : 1000;

            int t;
            int is_cmd_timeout = 1;
            if (at_queue_timeout(pvt, &t)) {
                is_cmd_timeout = 0;
            }

            ast_mutex_unlock(&pvt->lock);

            if (is_cmd_timeout) {
                if (t <= 0) {
                    if (check_taskprocessor(tps, dev)) {
                        if (ast_taskprocessor_push(tps, restart_monitor_taskproc, pvt)) {
                            ast_debug(5, "[%s] Unable to restart monitor thread\n", dev);
                        }
                    }

                    if (ast_taskprocessor_push(tps, cmd_timeout_taskproc, pvt)) {
                        ast_debug(5, "[%s] Unable to handle timeout\n", dev);
                    }

                    t = UNHANDLED_COMMAND_TIMEOUT;
                    if (!at_wait(fd, &t)) {
                        continue;
                    }
                } else {
                    /*
                     * A response handled by the serializer does not wake this
                     * fd wait. Recheck within the phase budget instead of
                     * sleeping until an already completed command's deadline.
                     * Fast setup: <=500ms slice + 500ms idle, not a fixed period.
                     * A slice expiry is not a command timeout; keep its deadline.
                     */
                    const int sliced = qdc507_poll && t > poll_wait_ms;
                    if (sliced) {
                        t = poll_wait_ms;
                    }
                    if (!at_wait(fd, &t)) {
                        if (!sliced && ast_taskprocessor_push(tps, cmd_timeout_taskproc, pvt)) {
                            ast_debug(5, "[%s] Unable to handle timeout\n", dev);
                        }
                        continue;
                    }
                }
            } else {
                t = qdc507_poll ? poll_wait_ms : RESPONSE_READ_TIMEOUT;

                if (!at_wait(fd, &t)) {
                    if (check_taskprocessor(tps, dev)) {
                        if (ast_taskprocessor_push(tps, restart_monitor_taskproc, pvt)) {
                            ast_debug(5, "[%s] Unable to restart monitor thread\\n", dev);
                        }
                    }

                    if (qdc507_poll) {
                        if (ast_taskprocessor_push(tps,
                                                   qdc507_clcc_poll_taskproc,
                                                   pvt)) {
                            ast_debug(5,
                                      "[%s][QDC507] Unable to poll CLCC\\n",
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
            }
        }

        /* FIXME: access to device not locked */
        int iovcnt = at_read(dev, fd, &rb);
        if (iovcnt < 0) {
            break;
        }

        if (!ast_mutex_trylock(&pvt->lock)) {
            PVT_STAT(pvt, d_read_bytes) += iovcnt;
            ast_mutex_unlock(&pvt->lock);
        }

        struct iovec iov[2];
        size_t skip = 0u;

        while ((iovcnt = at_read_result_iov(dev, &read_result, &skip, &rb, iov, result)) > 0) {
            const size_t len = at_combine_iov(result, iov, iovcnt);
            rb_read_upd(&rb, len + skip);
            skip = 0u;
            if (!len) {
                continue;
            }

            struct at_response_taskproc_data* const tpdata = at_response_taskproc_data_alloc(pvt, result);
            if (tpdata) {
                if (ast_taskprocessor_push(tps, at_response_taskproc, tpdata)) {
                    ast_log(LOG_ERROR, "[%s] Fail to handle response\n", dev);
                    ast_free(tpdata);
                    goto e_restart;
                }
            }
        }
    }

    ast_mutex_lock(&pvt->lock);

e_cleanup:
    if (!pvt->initialized) {
        // TODO: send monitor event
        ast_verb(3, "[%s] Error initializing channel\n", dev);
    }
    /* it real, unsolicited disconnect */
    pvt->terminate_monitor = 0;

e_restart:
    pvt_disconnect(pvt);
    //	pvt->monitor_running = 0;
    ast_mutex_unlock(&pvt->lock);
}

static void* monitor_threadproc(void* _pvt)
{
    struct pvt* const pvt = _pvt;
    monitor_threadproc_pvt(pvt);
    /* TODO: wakeup discovery thread after some delay */
    return NULL;
}

int pvt_monitor_start(struct pvt* pvt)
{
    if (ast_pthread_create_background(&pvt->monitor_thread, NULL, monitor_threadproc, pvt) < 0) {
        pvt->monitor_thread = AST_PTHREADT_NULL;
        return 0;
    }

    return 1;
}

void pvt_monitor_stop(struct pvt* pvt)
{
    if (pvt->monitor_thread == AST_PTHREADT_NULL) {
        return;
    }

    pvt->terminate_monitor = 1;
    pthread_kill(pvt->monitor_thread, SIGURG);

    {
        const pthread_t id = pvt->monitor_thread;
        SCOPED_LOCK(pvt_lock, &pvt->lock, ast_mutex_unlock, ast_mutex_lock);  // scoped UNlock
        pthread_join(id, NULL);
    }

    pvt->terminate_monitor = 0;
    pvt->monitor_thread    = AST_PTHREADT_NULL;
}
