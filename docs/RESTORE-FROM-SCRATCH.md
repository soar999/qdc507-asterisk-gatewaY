# QDC507 Asterisk Gateway — 从零恢复指南

> 目标：在一台新的或重装后的 Synology DS918+ 上，使用本仓库中的代码、配置模板和已固定校验值的运行依赖，恢复当前 QDC507 + Asterisk 语音/SMS 网关。

## 1. 当前可恢复范围

本仓库保存了当前系统的主要可重建部分：

- Asterisk 22 Docker 构建定义
- 最终 QDC507 `chan_quectel` patch
- Asterisk 配置及脱敏模板
- QDC507 USB / ADB / 宿主音频恢复脚本
- QDC507 设备侧 voice runtime
- QDC507 通话级音频 helper
- SMS 队列消费者和 systemd timer
- 冷启动与人工热插拔恢复逻辑
- SIP REGISTER 防护脚本
- Asterisk DNS Manager 配置
- File Station 安全编辑/部署脚本
- Asterisk 日志控制及 100 MiB 大小检查轮转脚本

仓库**不保存**：

- 生产 SIP 密码
- 真实生产 `pjsip.conf`
- 真实生产 `extensions.conf`
- 私钥
- IMEI / IMSI / ICCID
- 短信正文和 SMS 队列数据
- Asterisk 日志、抓包、运行时私有状态

因此，灾难恢复时仍需重新填写本机的生产凭据、DDNS/网络参数和拨号计划中的环境特定值。

## 2. 已验证的平台边界

当前运行环境：

- Synology DS918+
- DSM 7
- NAS Linux kernel：`4.4.180+`
- Docker / Container Manager
- Python 3.8
- Baiwang QDC507
- QDC507 模块侧语音 runtime 曾验证于模块内核 `3.18.44`

`install/install-host-runtime.sh` 会拒绝与 `4.4.180+` 不一致的 NAS kernel，因为仓库中的 `option.ko`、`usb_wwan.ko` 是针对当前平台固定的二进制。

如果 DSM、Docker 套件、NAS 型号、内核或 QDC507 firmware 发生变化，不应直接绕过校验；先重新验证兼容性。

## 3. 关键运行依赖及 SHA-256

### NAS / host

- `runtime/host/adb`
  - `a902be8f45c6c62e76c9efaf6947a0fa747c9cabd89a2ac8e0d16ecb30b3ed01`
- `runtime/host/option.ko`
  - `944696cdcebaf0cf3195e2029834b83e12c35d30200801f52c48f849e46025bc`
- `runtime/host/usb_wwan.ko`
  - `dff9cf76bb317e484b2314ad0a617d29106e6ce1d751417e33f692eb9a633002`

### QDC507 device-side voice runtime

- `runtime/device/qdc507_aprv3.ko`
  - `3d82d3dec4f1e323201bba87156df9d41438e08314097353f2607f9117211d4a`
- `runtime/device/qdc507_voice.ko`
  - `ed3821682d5309969a01c764192c83feff9669c61ef237c69475cd1619cf296c`
- `runtime/device/mavo-pcm-bridge.armv7`
  - `88d47c15e61d1428a59c821fed804c2e6490e82859a085062f21966b58d167fc`

仓库中同时保存 `SHA256SUMS`。恢复前必须校验。

> 这些二进制是灾难恢复所需的固定运行依赖。仓库如果未来改为 public，应先单独核实所有第三方二进制的再分发许可。

## 4. 推荐目录结构

当前 Compose 和脚本按以下生产路径设计：

```text
/volume2/docker/qdc507/
└── docker-compose.yml

/volume2/docker/asterisk/qdc507-current/
├── v0/                         # Asterisk live config
├── v0-edit/                    # File Station 编辑副本
├── v1/                         # /var/log/asterisk
├── v2/                         # /var/spool/asterisk
│   └── sms-queue/
│       ├── pending/
│       ├── work/
│       └── delivered/
├── v3/                         # /var/lib/asterisk/keys
├── v4/                         # /var/lib/asterisk/phoneprov
├── v5/                         # /var/lib/asterisk/sounds
├── helpers/
│   ├── adb
│   ├── qdc507-call-audio-start.sh
│   └── qdc507-call-audio-stop.sh
└── modules.hardware.runtime.conf
```

宿主机恢复组件安装到：

```text
/usr/local/libexec/qdc507/
├── adb
├── boot/
│   ├── option.ko
│   ├── usb_wwan.ko
│   └── qdc507-bind-if2.sh
└── runtime/
    ├── qdc507_aprv3.ko
    ├── qdc507_voice.ko
    └── mavo-pcm-bridge.armv7
```

恢复脚本安装到 `/usr/local/sbin/`。

## 5. 克隆仓库

在维护用 Mac 或 NAS 可写目录中：

```bash
git clone <YOUR_PRIVATE_REPOSITORY_URL>
cd qdc507-asterisk-gatewaY
```

建议仓库保持 private，直到第三方二进制的公开再分发许可全部核实。

## 6. 校验 runtime

```bash
cd runtime/host
sha256sum -c SHA256SUMS

cd ../device
sha256sum -c SHA256SUMS

cd ../..
```

所有项目都应显示 `OK`。

## 7. 构建 Asterisk + chan_quectel 镜像

最终构建上下文：

```text
build/clccsafe4/
├── Dockerfile.qdc507
└── qdc507_patch.py
```

构建：

```bash
cd build/clccsafe4

sudo docker build \
  -f Dockerfile.qdc507 \
  -t qdc507-asterisk:22.10.1-qdc507-clccsafe4 \
  .

cd ../..
```

该构建链固定 upstream / base 版本并应用当前 QDC507 patch。构建时需要能够访问其上游源。

## 8. 创建生产目录

```bash
sudo mkdir -p \
  /volume2/docker/qdc507 \
  /volume2/docker/asterisk/qdc507-current/v0 \
  /volume2/docker/asterisk/qdc507-current/v1 \
  /volume2/docker/asterisk/qdc507-current/v2/sms-queue/pending \
  /volume2/docker/asterisk/qdc507-current/v2/sms-queue/work \
  /volume2/docker/asterisk/qdc507-current/v2/sms-queue/delivered \
  /volume2/docker/asterisk/qdc507-current/v3 \
  /volume2/docker/asterisk/qdc507-current/v4 \
  /volume2/docker/asterisk/qdc507-current/v5 \
  /volume2/docker/asterisk/qdc507-current/helpers
```

复制 Compose：

```bash
sudo cp docker-compose.yml /volume2/docker/qdc507/docker-compose.yml
```

## 9. 创建 Asterisk 生产配置

把安全的基础配置复制到 live config：

```bash
sudo cp config/asterisk.conf \
        config/dnsmgr.conf \
        config/logger.conf \
        config/quectel.conf \
        config/rtp.conf \
        /volume2/docker/asterisk/qdc507-current/v0/

sudo cp config/modules.hardware.runtime.conf \
        /volume2/docker/asterisk/qdc507-current/modules.hardware.runtime.conf
```

从模板创建生产版：

```bash
sudo cp config/pjsip.conf.example \
  /volume2/docker/asterisk/qdc507-current/v0/pjsip.conf

sudo cp config/extensions.conf.example \
  /volume2/docker/asterisk/qdc507-current/v0/extensions.conf
```

然后手工填写：

- SIP 密码
- DDNS / external signaling / media 地址
- 本地网段
- trunk / endpoint 参数
- 拨号计划中的真实网络和业务参数

**不要**把生产 `pjsip.conf` 和 `extensions.conf` 提交回 Git。

统一 live config 权限：

```bash
sudo chown -R root:root /volume2/docker/asterisk/qdc507-current/v0
sudo find /volume2/docker/asterisk/qdc507-current/v0 -type d -exec chmod 755 {} \;
sudo find /volume2/docker/asterisk/qdc507-current/v0 -type f -exec chmod 644 {} \;

sudo chown root:root \
  /volume2/docker/asterisk/qdc507-current/modules.hardware.runtime.conf
sudo chmod 644 \
  /volume2/docker/asterisk/qdc507-current/modules.hardware.runtime.conf
```

不要给 live `v0` 添加 DSM File Station ACL。

## 10. 安装 host / device runtime 和恢复脚本

在仓库根目录，以 root 执行：

```bash
sudo /bin/sh install/install-host-runtime.sh
```

该脚本：

- 校验 NAS kernel
- 校验 runtime SHA-256
- 安装 `adb`
- 安装 `option.ko`、`usb_wwan.ko`
- 安装 QDC507 voice runtime
- 安装 coldstart / hotplug recovery 脚本
- 安装 SMS 脚本与 systemd unit
- 安装配置部署与日志维护脚本
- 安装 Compose 所需的 helper 副本

该脚本**不会**启动容器、操作 USB，也不会自动启用 SMS timer。

## 11. 部署 / 创建容器

```bash
cd /volume2/docker/qdc507

sudo docker-compose \
  -f docker-compose.yml \
  config >/dev/null \
  && echo COMPOSE_OK
```

确认 `COMPOSE_OK` 后：

```bash
sudo docker-compose \
  -f docker-compose.yml \
  up -d --force-recreate qdc507-hardware
```

等待 Asterisk 初始化后：

```bash
sleep 20

sudo docker exec qdc507-hardware \
  asterisk -rx 'core show version'
```

## 12. DNS Manager

`config/dnsmgr.conf`：

```ini
[general]
enable=yes
refreshinterval=60
```

检查：

```bash
sudo docker exec qdc507-hardware \
  asterisk -rx 'dnsmgr status'
```

期望：

- `DNS Manager: enabled`
- `Refresh Interval: 60 seconds`
- `Number of entries` 大于 0（具体数量取决于 PJSIP transport 配置）

这样使用域名配置的 `external_signaling_address` / `external_media_address` 可以跟随 DDNS 更新，不需要因公网 IP 正常变化而频繁重启 Asterisk。

## 13. Asterisk 日志

当前 Compose 使用：

```text
-vf
```

生产环境默认：

```text
Debug level: 0
```

检查：

```bash
sudo docker exec qdc507-hardware \
  asterisk -rx 'core show settings' | grep -i debug
```

`logger.conf` 当前设计：

```text
messages  -> WARNING ERROR
security  -> WARNING ERROR
debug     -> DEBUG
```

轮转后使用 gzip 压缩。

日志大小检查脚本：

```text
/usr/local/sbin/qdc507-asterisk-log-sizecheck.sh
```

当前阈值为 100 MiB。

在 DSM Task Scheduler 中可建立 root 任务，每天执行一次：

```bash
/bin/sh /usr/local/sbin/qdc507-asterisk-log-sizecheck.sh
```

该任务只是检查大小；不到阈值不会轮转。

## 14. File Station 安全编辑工作流

不要再给 `/volume2/docker/asterisk/qdc507-current/v0` 设置 DSM File Station ACL，否则容器中的 `asterisk` 用户可能看到 `d---------` / `Permission denied`。

创建编辑副本：

```bash
BASE=/volume2/docker/asterisk/qdc507-current

sudo mkdir -p "$BASE/v0-edit"

sudo sh -c '
cd /volume2/docker/asterisk/qdc507-current/v0 &&
tar cf - . | (
  cd /volume2/docker/asterisk/qdc507-current/v0-edit &&
  tar xf -
)
'

sudo cp "$BASE/modules.hardware.runtime.conf" \
        "$BASE/v0-edit/modules.hardware.runtime.conf"

sudo rm -f "$BASE/v0-edit/modules.conf"

sudo chown -R admin:root "$BASE/v0-edit"
sudo find "$BASE/v0-edit" -type d -exec chmod 755 {} \;
sudo find "$BASE/v0-edit" -type f -exec chmod 644 {} \;
```

以后只在 File Station 修改 `v0-edit`。

部署：

```bash
sudo /usr/local/sbin/qdc507-config-deploy.sh
```

DSM Task Scheduler 中如需一个手动任务，可使用 root 用户，命令：

```bash
/bin/sh /usr/local/sbin/qdc507-config-deploy.sh
```

该部署脚本不会自动重启 Asterisk。

## 15. SMS timer

先确认：

```bash
sudo systemctl daemon-reload
sudo systemctl enable qdc507-sms-tick.timer
sudo systemctl start qdc507-sms-tick.timer
```

检查：

```bash
sudo systemctl status qdc507-sms-tick.timer --no-pager
```

期望 `enabled` 且 `active (waiting)`。

SMS 队列路径：

```text
/volume2/docker/asterisk/qdc507-current/v2/sms-queue/
├── pending/
├── work/
└── delivered/
```

`delivered` 仅表示本地投递命令成功，并不代表手机终端提供了送达回执。

## 16. NAS 冷启动任务

当前冷启动入口：

```text
/usr/local/sbin/qdc507-coldstart.py
```

DSM Task Scheduler 建立 **root / 开机触发**任务：

```bash
/usr/bin/python3.8 /usr/local/sbin/qdc507-coldstart.py
```

冷启动链负责：

- host USB/serial 准备
- IF2
- host audio
- ADB listener
- `qdc507-hardware` 启动/验证流程

报告：

```text
/var/lib/qdc507-coldstart-last.txt
```

恢复后必须实际做一次 NAS 冷启动验收，不要仅凭脚本退出码认定成功。

## 17. QDC507 热拔插 / 模块侧恢复

当前人工入口：

```bash
sudo /usr/bin/python3.8 \
  /usr/local/sbin/qdc507-one-task.py
```

运行前：

- 确认没有正在通话
- 当前正式设计要求 `qdc507-hardware` 在入口时已经 running
- 不要同时运行多个恢复任务

它会按需要处理：

- USB generation 稳定等待
- host audio 修复
- IF2 映射
- IF5 / ADB 恢复
- QDC507 voice runtime 部署/验证

人工恢复报告：

```text
/var/lib/qdc507-manual-recover-last.txt
```

最终必须以实际通话双方有声为准。

## 18. Router SIP REGISTER 防护

仓库：

```text
router/qdc507-sip-guard.sh
```

部署前修改：

```sh
NAS_IP="..."
SIP_PORT="..."
```

该脚本：

- 只处理目标 SIP UDP 端口
- 只对 `REGISTER` 做匹配
- 使用 `hashlimit` 按 source IP 限速
- 当前阈值约为 60 REGISTER/minute，burst 20
- 其他 SIP 流量 `RETURN`
- 重复运行不会不断叠加 hook

当前 ASUS stock firmware 的实测结果是：`service restart_firewall` 后不会自动调用我们创建的 JFFS hook。因此在该固件上，防火墙重建或路由器重启后需要手工重新运行：

```bash
/jffs/scripts/qdc507-sip-guard.sh
```

如以后改用支持可靠持久 hook 的固件，再重新设计自动恢复。

## 19. 恢复后的验收顺序

建议严格按以下顺序。

### 19.1 容器和 Asterisk

```bash
sudo docker ps | grep qdc507-hardware

sudo docker exec qdc507-hardware \
  asterisk -rx 'core show version'
```

### 19.2 QDC507

```bash
sudo docker exec qdc507-hardware \
  asterisk -rx 'quectel show device state quectel0'
```

至少检查：

- `State: Free`
- `Voice: Yes`
- `SMS: Yes`
- cellular registration 正常

不要把命令输出中的 IMEI / IMSI / ICCID 提交到 Git。

### 19.3 DNS

```bash
sudo docker exec qdc507-hardware \
  asterisk -rx 'dnsmgr status'
```

应为 enabled / 60s / entries > 0。

### 19.4 Logger

```bash
sudo docker exec qdc507-hardware \
  asterisk -rx 'logger show channels'

sudo docker exec qdc507-hardware \
  asterisk -rx 'core show settings' | grep -i debug
```

Debug level 应为 0。

### 19.5 Echo

用 SIP 客户端拨：

```text
*43
```

必须能听到回声。

### 19.6 真实电话

分别验证：

- cellular -> SIP 来电
- SIP -> cellular 外呼
- 双方音频
- 挂机状态
- 连续多次通话

### 19.7 SMS

分别验证：

- cellular -> SIP / Groundwire
- SIP / Groundwire -> cellular

### 19.8 热拔插

无通话时：

1. 拔出 QDC507
2. 重新插入
3. 等待 USB 稳定
4. 手工运行 `qdc507-one-task.py`
5. 再测电话和 SMS

### 19.9 真正冷启动

最终必须做一次 NAS 关机/开机或完整重启测试，再验证：

- `/dev/qdc507-at`
- host audio nodes
- ADB
- container
- QDC507 voice runtime
- Asterisk
- DNS Manager
- SMS timer
- 电话双向音频
- SMS 双向收发

## 20. 固定哈希与版本变化

恢复脚本对部分关键文件进行了 SHA-256 固定，包括 Docker binary 和 runtime。

如果全新 DSM 上出现类似：

```text
DOCKER_BINARY_CHANGED
SOURCE_HASH_CHANGED
KERNEL_CHANGED
```

不要简单删除校验或绕过。

正确流程：

1. 确认新文件来自可信来源
2. 确认版本变化是预期的
3. 在隔离环境验证功能
4. 重新计算 SHA-256
5. 有意识地更新 pin
6. 提交新的 Git 版本

这套 pin 是安全边界的一部分。

## 21. 关于“是否可以完全从 GitHub 重建”

在与当前平台兼容的前提下，本仓库现在保存了：

- Asterisk 构建链
- QDC507 patch
- 生产所需配置模板
- host USB 驱动
- ADB
- QDC507 voice runtime
- recovery scripts
- SMS components
- helper scripts
- Compose
- router rule
- operational maintenance scripts

因此，它已经可以作为**当前系统的灾难恢复基线**。

但“完全恢复”仍有几个外部前提：

1. 能访问 Docker / upstream 构建依赖；
2. DS918+ / DSM / kernel 与固定二进制兼容；
3. 重新填入生产凭据和网络参数；
4. GitHub 中未保存的业务私密数据（例如短信历史）不是恢复目标；
5. 新版本 Docker / DSM 如改变 binary SHA，需要重新验证并更新安全 pin；
6. 恢复完成后必须做真实冷启动、电话和 SMS 验收。

只有通过这些实际验收，才算恢复成功。

## 22. 当前已知边界

当前系统曾完成真实冷启动、通话和 SMS 验证；随后项目又经历了 Asterisk 配置 ACL 修复、DNS Manager 启用、日志策略和 Compose 启动参数等更新。

因此，任何一次“从 GitHub 从零恢复”之后，都应重新执行本文件第 19 节的完整验收，而不能只依赖历史成功记录。
