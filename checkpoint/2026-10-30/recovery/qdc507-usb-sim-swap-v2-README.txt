QDC507 USB 拔插 / 换 SIM 恢复入口修复 v2 (2026-10-29)
===================================================

用途
- 维持原有命令：sudo /bin/sh /volume2/docker/asterisk/qdc507-current/recovery/qdc507-usb-recover.sh
- 原有热插拔协调器和 oneclick 完全不修改。
- 自动检查/恢复 ADB 设备重新发现（必要时仅重启 ADB server 一次），之后执行原有 hotplug -> oneclick 恢复，再确保 Guard16 v2。
- 拒绝存在 pending、USB 序列号改变、NAS 重新启动、非原容器、SIM/LTE 未注册、活动通话或 SMS worker 忙碌的状态。
- 当前已在恢复正常的生产状态上进行代码审查与本地模拟测试；新包装脚本本身尚未经过下一次真实 USB 拔插验收。

文件均放到 NAS：/volume2/docker/asterisk/qdc507-16k-lab/ 根目录
  qdc507-usb-recover-v2.sh
  qdc507-usb-wrapper-install-v2.py

安装：
P=/volume2/docker/asterisk/qdc507-16k-lab
sudo chown root:root "$P/qdc507-usb-recover-v2.sh" "$P/qdc507-usb-wrapper-install-v2.py"
sudo chmod 700 "$P/qdc507-usb-recover-v2.sh" "$P/qdc507-usb-wrapper-install-v2.py"
sudo /usr/bin/python3.8 -I -B "$P/qdc507-usb-wrapper-install-v2.py" --check
sudo /usr/bin/python3.8 -I -B "$P/qdc507-usb-wrapper-install-v2.py" --install

安装之后，生产正常且无通话时，只读检查：
sudo /bin/sh /volume2/docker/asterisk/qdc507-current/recovery/qdc507-usb-recover.sh --check
正常时要同时看到 USB_SWAP_READ_ONLY_CHECK_OK=1、SIM_REGISTERED=1 和 ADB_STATUS=online。

以后实际更换 SIM：
1. 结束所有通话，确认短信工作进程空闲。
2. 拔 USB、正确安装 SIM、重新插入 QDC507，待 USB 枚举/模块网络注册。
3. 执行：
sudo /bin/sh /volume2/docker/asterisk/qdc507-current/recovery/qdc507-usb-recover.sh
4. 成功标记 USB_SIM_SWAP_GUARD16_V2_RECOVERY_COMPLETE=1 后，再真实通话测试双向音频/自动挂断。

失败保护：
- 失败后不要自动再跑，不要删除 oneclick pending，不要手改 USB generation JSON。
- 遇到 SIM_NOT_REGISTERED... 请先检查 SIM 插卡方向、PIN 和模块注册情况；此时程序不会启动 oneclick 的写入恢复。
- oneclick 若已启动且失败，会保留 pending 供人工检查；不可盲目删除。
- 这套入口用于 USB 拔插换 SIM，不取代 NAS 冷启动的 coldboot-production-v3.py --boot。

回退包装脚本（不会回退 USB 设备状态）：
sudo /usr/bin/python3.8 -I -B "$P/qdc507-usb-wrapper-install-v2.py" --rollback

实现限制与前提：
- 只针对现有固定 NAS、同一 QDC507 和原 Docker 容器；不支持更换硬件或重建容器。
- SIM 必须在恢复写入阶段前已由现有 Asterisk 确认 LTE 注册，且 State=Free/Voice=Yes/SMS=Yes。
- 不会在错误插卡、未知 pending、部分模块状态异常等情况下强行继续。
- 一旦某阶段出现无法确定的超时，必须人工检查日志和当前硬件状态。
