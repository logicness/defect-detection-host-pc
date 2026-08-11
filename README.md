# Industrial Host PC - 工业缺陷检测上位机（RK3568 版）

工业表面缺陷检测系统的上位机软件（Windows PC 端），配套 RK3568 / Jetson 系列下位机使用。
UI 严格按 5 张设计稿实现：实时检测 / 参数设置 / 历史记录 / 通信设置 / 运行日志。

## 功能特性（v1.1）

- **实时检测（图1）**：相机设置 / 检测参数 / ROI 设置（增删改+启禁）/ 开始停止检测 /
  实时预览（滚轮缩放 30%~500%、中键平移、双击还原、ROI 绿框、缺陷红框、NG 浮窗）/
  检测结果 KPI（总数/OK/NG/良率）/ 当前结果详情 / 检测历史 / 通信存储模型卡 / 运行日志
- **三推理源**：下位机 TCP 推理（连接时自动启用）> PC 本地模型推理（选择本地 .onnx/.pt
  即启用）> 本地模拟推理（合成轴承工件视频流 + 周期缺陷），整机 UI 可离线完整演示
- **参数设置（图2）**：相机参数 / 检测模型（模型文件/输入尺寸/阈值/加载）/
  图像预处理（缩放/灰度归一化/去噪/对比度）/ ROI 可视化编辑（画布拖拽+表单双向同步）/
  存储设置 / 应用设置（下发 conf+ROI 到下位机）/ 保存配置 / 恢复默认
- **历史记录（图3）**：多条件查询（时间/产品/结果/缺陷类型/路径关键词）/ KPI /
  分页表格（15/30/50 每页）/ 统计图表（近 14 天趋势折线 + 缺陷类型饼图）/
  记录详情（预览图+字段+查看原图+打开目录）/ CSV 导出
- **通信设置（图4）**：PLC 通信（Modbus TCP：连接/断开/测试）/ I/O 信号映射表（状态灯）/
  串口通信（pyserial 真实收发：打开/刷新/十六进制收发）/ 通信测试 /
  通信状态（TX/RX/ERR/RTT KPI + 收发日志）
- **运行日志（图5）**：级别/模块双分段筛选 + 时间 + 关键词 / 实时刷新开关 /
  日志详情（线程/代码）/ 系统状态（运行时间/帧率/CPU/内存/NPU/磁盘）/ 导出 / 统计栏
- **模型管理**：Nano 模型清单刷新/切换 + PC 本地模型选择（.onnx/.pt 自动启用本地推理）
- **本地图片检测**：菜单入口 → 选任意图片即检测（有模型真实推理、无模型模拟演示），
  红框叠加 ROI，可保存标注图，结果入历史
- **产线联动**：NG 自动 PLC 剔除（产线运行中才剔除）/ 连续 NG 告警（阈值 5，弹窗+日志）/
  NG 图片异步归档（原图+标注图+CSV）/ 低置信案例归档
- 暗色主题（16px 字号体系）、无边框窗口、边缘拖拽缩放、启动自动连接（TCP+PLC）、心跳看门狗重连

## 技术栈

- PyQt5 + QPainter + QSS（Fusion 暗色主题）
- socket + QThread + pyqtSignal（TCP：4 字节大端长度头 + UTF-8 JSON）
- Modbus TCP 纯 socket 实现（零第三方依赖）
- pyserial 串口真实收发
- SQLite（defect_records + production_stats，异步写库线程）
- OpenCV（模拟帧源渲染 / NG 标注）

## 快速开始

```bash
# 安装依赖（Python 3.13 / Anaconda base）
pip install -r requirements.txt

# 启动（推荐双击 启动上位机.vbs，无黑框）
python main.py

# 演示数据（历史记录页 1000 条，995 OK / 5 NG）
python tools/seed_demo.py

# PLC 联调：先起模拟器，再在通信设置页点「测试通信」
python tools/plc_sim.py 2000
```

- 默认下位机 `192.168.1.101:8888`、PLC `192.168.1.200:2000`（启动自动连接，失败自动重连）
- 未连接下位机时点「开始检测」= 本地模拟推理演示模式

## 目录结构

```
Host PC/
├── main.py                  # 入口：无边框主窗口 + 5 Tab + 状态栏 + 菜单 + 全局接线
├── core/
│   ├── controller.py        # 主控制器（TCP/PLC/DB + 结果管线：KPI/告警/剔除/写库）
│   ├── tcp_client.py        # TCP 客户端（心跳看门狗 + 指数退避重连）
│   ├── plc_client.py        # Modbus TCP 客户端（纯 socket，轮询产线状态）
│   ├── serial_client.py     # 串口客户端（pyserial 后台读线程）
│   ├── database.py          # SQLite（查询/分页/KPI/导出/清理/日统计/缺陷类型统计）
│   ├── frame_source.py      # 帧源抽象 + 模拟帧源（合成工件图+周期缺陷）
│   ├── stream_engine.py     # 实时流引擎（tcp/local/sim 三推理模式，帧率节流）
│   ├── ng_saver.py          # NG 异步归档（原图+标注图+CSV+低置信）
│   ├── local_infer.py       # PC 本地推理引擎（.onnx onnxruntime / .pt ultralytics）
│   ├── local_postprocess.py # YOLOv8 ONNX 后处理（NMS）
│   └── config.py            # 配置持久化 data/host_config.json
├── components/
│   ├── title_bar.py         # 自定义标题栏（三状态灯+模型/相机标签+菜单）
│   ├── common_widgets.py    # Card/KPICard/StatusLight/Toggle/StyledTable/SegGroup 等
│   ├── image_preview.py     # 预览组件（缩放/平移/ROI/缺陷框/NG 浮窗）
│   ├── stats_charts.py      # 统计图表（近14天趋势折线 + 缺陷类型饼图）
│   ├── roi_editor.py        # ROI 可视化编辑（画布拖拽/缩放 + 表单双向同步）
│   ├── model_manager_dialog.py  # 模型管理对话框（Nano 清单 + PC 本地模型）
│   └── local_image_detect.py    # 本地图片检测对话框（选图→推理→标注→保存）
├── pages/                   # 5 个页面（对应 5 张设计稿）
├── assets/qss/dark_theme.qss
├── tools/                   # seed_demo 演示数据 / plc_sim Modbus 模拟器
├── data/                    # 运行时数据（db/配置，自动创建）
├── 启动上位机.bat / .vbs
└── requirements.txt
```

## 通信协议

**TCP（下位机推理）**：4 字节大端长度头 + UTF-8 JSON。
消息类型：`detect_request`(image_base64) / `detect_response`(detections) /
`heartbeat`+`heartbeat_ack` / `control`(conf_thres/iou_thres/rois) /
`model_list_request` / `model_load_request`。

**Modbus TCP（PLC）**：MBAP 头 + PDU。功能码 0x01/0x03/0x05。
地址映射：线圈 0=产线运行 1=故障 2=剔除触发(写) 3=剔除确认；寄存器 0=合格计数 1=缺陷计数。

## 版本历史

- **v1.1（2026-08-11）**：参考 Nano 版上位机完善。
  - UI 全面放大：全局 16px 字号体系（按钮 min-height 36px / 输入框 36px / Tab 17px /
    表格 16px / KPI 44px / 状态栏 15px），卡片与间距统一加大
  - 历史记录页新增统计图表（近 14 天检测趋势折线 + 缺陷类型饼图，matplotlib 嵌入）
  - ROI 可视化编辑：画布拖拽移动/缩放手柄 + 坐标表单双向同步（替换原纯表单对话框）
  - 本地模型推理：选择本地 .onnx/.pt 模型即启用 PC 端推理（onnxruntime CPU），
    推理源优先级：下位机 TCP > 本地模型 > 本地模拟
  - 模型管理对话框：Nano 模型清单刷新/切换 + PC 本地模型选择
  - 本地图片检测：菜单「本地图片检测」→ 选任意图片即检测（有模型走真实推理、
    无模型走模拟演示），红框叠加 ROI，可保存标注图，结果入历史
- **v1.0（2026-08-07）**：首版。5 页 UI 按设计稿实现；双推理源（TCP+本地模拟）；
  PLC Modbus 通信 + I/O 映射 + NG 剔除；串口真实收发；SQLite 分页历史 + 导出；
  NG/低置信异步归档；运行日志筛选 + 系统状态；配置持久化；演示数据与 PLC 模拟器工具
