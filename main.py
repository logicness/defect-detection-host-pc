"""
工业缺陷检测上位机 - 应用入口
无边框主窗口 + 自定义标题栏 + 5 Tab 导航 + 状态栏
接线：控制器(TCP/PLC/DB) ↔ 5 页面 ↔ 实时流引擎 ↔ NG 归档
"""
import os
import sys
import time
import traceback

import cv2
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QTabWidget,
    QLabel, QStatusBar, QSizeGrip, QMessageBox, QFileDialog
)
from PyQt5.QtCore import Qt, QRect, QTimer
from PyQt5.QtGui import QFont, QImage

from components.title_bar import TitleBar
from pages import (
    RealtimeDetectPage, ParamSettingPage, HistoryRecordPage,
    CommSettingPage, RunLogPage
)
from core.controller import AppController
from core.stream_engine import StreamEngine
from core.model_manager_ctl import ModelManagerCtl
from core.ng_saver import NGSaver
from core import config as cfg_mod

_EDGE_MARGIN = 6


def _popup_info(parent, title: str, text: str):
    """信息弹窗（offscreen 无头环境跳过，避免崩溃）"""
    if QApplication.platformName() != "offscreen":
        QMessageBox.information(parent, title, text)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setMinimumSize(1600, 1000)
        self.resize(1920, 1080)

        central = QWidget()
        central.setMouseTracking(True)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题栏
        self.title_bar = TitleBar("工业缺陷检测上位机")
        self.title_bar.window_minimized.connect(self.showMinimized)
        self.title_bar.window_maximized.connect(self._toggle_maximize)
        self.title_bar.window_closed.connect(self.close)
        self.title_bar.menu_requested.connect(self._on_menu)
        layout.addWidget(self.title_bar)
        self.title_bar.set_model_tag("Product_A_v1")
        self.title_bar.set_camera_tag("Camera_01")

        # 核心对象
        self.cfg = cfg_mod.load_config()
        self.controller = AppController()
        # 同步下位机当前模型名，用于 class_id → 类别名 映射
        self.controller.nano_model_name = self.cfg.get(
            "state", {}).get("last_nano_model", "")
        self.model_ctl = ModelManagerCtl(self.controller.tcp)
        # 默认 infer_mode 用 tcp，禁止未配置时误入模拟演示
        self.stream_engine = StreamEngine(infer_mode="tcp")
        self.ng_saver = NGSaver(self.cfg.get("storage", {}).get(
            "save_path", "D:/Inspect/Images"))
        self._last_frame = None
        self._plc_running = False
        # 本地图片模式：加载图片后直接显示在预览区，点「开始检测」做单帧推理
        self._local_image = None          # numpy BGR 帧
        self._local_image_path = ""       # 图片路径
        self._local_image_active = False  # 是否正处于本地图片模式
        self._nano_active = False         # 是否检测到开发板通信（自动切换开发板模型）
        # 多图批量检测状态
        self._multi_image_paths = []      # 多次模式选中的图片列表
        self._multi_image_idx = 0         # 当前浏览下标
        self._multi_results = {}          # path -> {"dets": [...], "ms": float}
        self._batch_running = False
        self._batch_queue = []            # 待检测图片路径
        self._current_batch_path = ""     # 当前正在检测的图片
        self._nano_local_pending = ""     # 正在等待 Nano 本地图片结果的路径
        # 下位机图片检测（2026-08-24）：与本地图片同交互，图片驻留 Nano，走文件名指令
        self._nano_image_active = False       # 是否处于下位机图片模式
        self._nano_image_names = []           # 选中的下位机图片文件名（顺序同 _multi_image_paths）
        self._nano_image_dir = ""             # 下位机图片文件夹
        self._nano_file_pending = ""          # 正在等待下位机图片检测结果的路径
        self._nano_wait_preview_detect = ""   # 预览就绪后要自动检测的文件名
        self._nano_start_pending = False      # 预览未就绪时点开始检测 → 就绪后自动触发
        self._nano_preview_name = ""          # 当前已显示预览的下位机文件名
        self._stream_camera_active = False    # 是否处于产线流模式
        # 从配置恢复上次本地模型（必须是存在的 .onnx/.pt 模型文件）
        self._local_model = self.cfg.get("state", {}).get("last_local_model", "")
        if self._local_model and not (
                os.path.isfile(self._local_model) and
                self._local_model.lower().endswith((".onnx", ".pt"))):
            self._local_model = ""
        # 没有本地模型时，自动探测 nano 模型训练目录中的默认缺陷检测模型
        if not self._local_model:
            self._local_model = self._find_default_local_model()
            if self._local_model:
                self._save_state(last_local_model=self._local_model)

        # 5 个 Tab
        self.tab = QTabWidget()
        self.tab.setObjectName("mainTabs")
        self.page_realtime = RealtimeDetectPage()
        self.page_param = ParamSettingPage()
        self.page_history = HistoryRecordPage()
        self.page_comm = CommSettingPage()
        self.page_log = RunLogPage()
        self.tab.addTab(self.page_realtime, "实时检测")
        self.tab.addTab(self.page_param, "参数设置")
        self.tab.addTab(self.page_history, "历史记录")
        self.tab.addTab(self.page_comm, "通信设置")
        self.tab.addTab(self.page_log, "运行日志")
        layout.addWidget(self.tab, 1)

        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_left = QLabel("就绪　|　检测帧率: -- FPS")
        self.status_time = QLabel("")
        self.status_bar.addWidget(self.status_left)
        self.status_bar.addPermanentWidget(self.status_time)
        self.grip = QSizeGrip(self)
        self.status_bar.addPermanentWidget(self.grip)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(1000)
        self._tick_clock()

        # 配置回填 + ROI 共享
        self.page_param.apply_config(self.cfg)
        self.page_comm.apply_config(self.cfg)
        self._sync_cfg_to_realtime()
        self._rois = self.cfg.get("rois", [])
        self.page_realtime.set_rois(self._rois)
        self.page_param.set_rois(self._rois)

        # 恢复上次本地模型到界面
        if self._local_model:
            self.title_bar.set_model_tag(os.path.basename(self._local_model))
            self.page_param.set_cur_model(os.path.basename(self._local_model))
            self.page_realtime.set_cur_model(os.path.basename(self._local_model))

        self._wire()
        self._emit_startup_logs()
        self._auto_connect()

        # 边缘缩放状态
        self._maximized = False
        self._resize_dir = None

    # ================= 接线 =================
    def _wire(self):
        c = self.controller

        # 日志 → 运行日志页 + 实时页 mini 日志
        c.log_message.connect(self.page_log.append_log)
        c.log_message.connect(
            lambda lv, mod, msg: self.page_realtime.append_mini_log(lv, msg))

        # FPS → 状态栏 + 日志页
        self.stream_engine.fps_updated.connect(self._on_fps)
        c.fps_updated.connect(self.page_log.set_fps)

        # 状态灯
        c.status_changed.connect(self._on_status_changed)

        # TCP 调试日志（写文件便于诊断 pythonw 无控制台场景）
        c.tcp.log_message.connect(self._log_tcp_debug)
        c.tcp.connected.connect(lambda: self._log_tcp_debug("INFO", "SIGNAL connected"))
        c.tcp.disconnected.connect(lambda: self._log_tcp_debug("INFO", "SIGNAL disconnected"))

        # 开发板通信识别 → 自动切换模型（连接切开发板模型 / 断开切回本地模型）
        c.tcp.connected.connect(self._on_nano_connected)
        c.tcp.disconnected.connect(self._on_nano_disconnected)

        # Nano 系统状态轮询（连接时启动，断开停止）
        c.tcp.status_received.connect(self._on_nano_status)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5000)
        self._status_timer.timeout.connect(self._poll_nano_status)

        # 模型管理（下位机清单/切换 → 标题栏与页面联动）
        self.model_ctl.models_updated.connect(self._on_models_updated)
        self.model_ctl.load_result.connect(self._on_model_load_result)

        # 实时流
        self.stream_engine.frame_ready.connect(self._on_stream_frame)
        self.stream_engine.result_received.connect(
            lambda r: c.ingest_result(r.get("detections", []), r.get("frame")))
        self.stream_engine.log_message.connect(
            lambda lv, m: c.log_message.emit(lv, "检测", m))
        self.stream_engine.state_changed.connect(self.page_realtime.set_running)

        # 推理结果 → 实时页/历史页/通信页
        c.detection_result.connect(self._on_detection_result)
        c.ng_alarm.connect(self._on_ng_alarm)

        # 实时页
        self.page_realtime.start_requested.connect(self._on_start)
        self.page_realtime.stop_requested.connect(self._on_stop)
        self.page_realtime.save_image_requested.connect(self._on_save_image)
        self.page_realtime.local_image_requested.connect(self._on_local_image)
        self.page_realtime.nano_image_detect_requested.connect(self._on_nano_image_detect)
        self.page_realtime.roi_changed.connect(self._on_roi_changed)
        self.page_realtime.load_model_requested.connect(self._on_load_model)
        self.page_realtime.model_mgr_requested.connect(self._on_model_mgr)
        self.page_realtime.save_path_changed.connect(self._on_save_path_changed)
        self.page_realtime.reconnect_requested.connect(self._on_tcp_reconnect)
        self.page_realtime.conf_changed.connect(self._on_conf_changed)
        self.page_realtime.prev_image_requested.connect(self._on_nav_prev)
        self.page_realtime.next_image_requested.connect(self._on_nav_next)

        # 下位机图片检测：预览大图 + 单张检测结果回传（2026-08-24）
        c.tcp.nano_image_received.connect(self._on_nano_preview_image)
        c.tcp.nano_detect_received.connect(self._on_nano_detect_result)

        # 产线模拟流（2026-08-25）
        c.tcp.stream_frame_received.connect(self._on_stream_frame)
        c.tcp.stream_control_received.connect(self._on_stream_control)

        # 参数页
        self.page_param.params_apply_requested.connect(self._on_params_apply)
        self.page_param.save_config_requested.connect(self._on_save_config)
        self.page_param.reset_requested.connect(self._on_reset_config)
        self.page_param.roi_changed.connect(self._on_roi_changed)
        self.page_param.load_model_requested.connect(self._on_load_model)
        self.page_param.model_mgr_requested.connect(self._on_model_mgr)

        # 历史页
        self.page_history.query_requested.connect(self._on_history_query)
        self.page_history.export_requested.connect(self._on_history_export)
        self.page_history.report_requested.connect(self._on_history_report)
        self.page_history.clear_history_requested.connect(self._on_history_clear)

        # 通信页
        self.page_comm.plc_connect_requested.connect(self._on_plc_connect)
        self.page_comm.plc_disconnect_requested.connect(c.disconnect_plc)
        self.page_comm.test_comm_requested.connect(self._on_test_comm)
        c.plc.status_updated.connect(self._on_plc_status)

    # ================= 默认本地模型 =================
    def _find_default_local_model(self) -> str:
        """在 nano 模型训练目录中探测默认本地缺陷检测模型（NEU-DET 类别）"""
        candidates = [
            # NEU-DET 训练产物（类别: crazing/inclusion/patches/pitted_surface/rolled-in_scale/scratches）
            r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\runs\neu_yolov8s_e1003\weights\best.onnx",
            r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\runs\neu_yolov8s_e1003\weights\best.pt",
            r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\runs\neu_fixed_v8s\weights\best.pt",
            r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\runs\detect\guangdong_cam_v1\weights\best.pt",
            r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\yolov8s.pt",
            r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\NEU-DET-with-yolov8-main\yolov8s.pt",
        ]
        for p in candidates:
            if os.path.isfile(p) and p.lower().endswith((".onnx", ".pt")):
                return p
        # 兜底：递归扫描 runs 目录下最近的 best 模型
        base = r"D:\RK3568&Orin Nano\ORIN NANO\Model Training\runs"
        best_found = None
        latest = 0.0
        if os.path.isdir(base):
            for root, _dirs, files in os.walk(base):
                for f in files:
                    if f in ("best.onnx", "best.pt"):
                        full = os.path.join(root, f)
                        if "_trash_" in full:
                            continue
                        mtime = os.path.getmtime(full)
                        if mtime > latest:
                            latest, best_found = mtime, full
        return best_found or ""

    # ================= 启动 =================
    def _emit_startup_logs(self):
        msgs = [
            ("INFO", "系统", "软件启动"),
            ("INFO", "系统", "配置文件加载完成"),
        ]
        if self._local_model and os.path.isfile(self._local_model):
            msgs.append(
                ("INFO", "模型",
                 f"默认本地模型: {os.path.basename(self._local_model)}（选择本地图片即可检测）"))
            msgs.append(("INFO", "模型", "连接开发板后将自动切换为开发板模型"))
        else:
            msgs.append(("INFO", "系统", "等待连接设备或加载本地图片；未连接前不会启动模拟检测"))
        msgs.append(("INFO", "通信", "TCP 未自动连接，请点击「重新连接」手动连接下位机"))
        for lv, mod, msg in msgs:
            self.controller.log_message.emit(lv, mod, msg)

    def _auto_connect(self):
        """启动时自动连接 PLC；TCP 改为手动连接，避免未开机时持续重试卡进程"""
        t = self.cfg.get("tcp", {})
        self.controller.configure_tcp(
            t.get("host", "192.168.1.101"), t.get("port", 8888),
            heartbeat=t.get("heartbeat", 5), retries=t.get("retries", 3),
            timeout=t.get("timeout", 10))
        # TCP 不再自动连接：用户点击「重新连接」/模型管理「重新连接」时才连
        self._on_plc_connect()

    def _on_plc_connect(self):
        p = self.page_comm.get_plc_config()
        if self.controller.plc.is_running:
            self.controller.plc.disconnect()  # 停掉旧的重连循环
        self.controller.configure_plc(
            host=p["host"], port=p["port"], unit_id=p["unit_id"],
            timeout=p["timeout_ms"] / 1000.0, poll_interval=p["poll_ms"] / 1000.0)
        self.controller.connect_plc()

    # ================= 检测流 =================
    def _on_start(self):
        # 新一次检测开始时，允许接收推理结果
        self._detection_paused = False

        # 产线模拟流模式：订阅 + 启动产线（下位机自主检测，上位机只接收）
        if "产线流" in self.page_realtime.get_infer_source():
            self._on_start_stream_camera()
            return

        # 下位机图片模式：预览未就绪时挂起，预览回传后自动重入本方法
        if getattr(self, "_nano_image_active", False) and self._local_image is None:
            self._nano_start_pending = True
            self.status_left.setText("下位机图片: 预览加载中，就绪后自动检测...")
            return

        # 已加载本地图片 → 推理（单次/多次）
        if self._local_image is not None:
            if self.stream_engine.is_running:
                self._on_stop()
            self._local_image_active = True
            self.page_realtime.set_running(True)

            # 多次模式 + 多张图片 → 批量检测
            multi = self.page_realtime.get_detect_mode() == "多次检测"
            if multi and len(self._multi_image_paths) > 1:
                self._start_batch()
                return
            # 单次模式 或 多次模式只有一张 → 单张检测
            self._detect_local_image()
            return

        # 没有本地图片时的实时流启停控制
        if self.stream_engine.is_running:
            self._on_stop()
            return

        # 没有选择图片、也没有真实相机输入源 → 明确提示，绝不启动模拟流
        self._local_image_active = False
        self.controller.log_message.emit(
            "WARN", "检测", "没有可用的检测输入源，未启动检测")
        if QApplication.platformName() != "offscreen":
            QMessageBox.information(
                self, "开始检测",
                "当前没有可用的检测输入源，无法开始检测。\n\n"
                "请先点击「本地图片」选择图片进行检测。")

    @staticmethod
    def _is_torchscript_model(path: str) -> bool:
        """检测 .pt 文件是否为 TorchScript 格式（无法直接用 YOLO 推理）"""
        # 文件名是最快的判断方式
        if "torchscript" in os.path.basename(path).lower():
            return True
        try:
            import torch
            # TorchScript 模型用 torch.jit.load 能直接加载
            # 普通 PyTorch 权重用 torch.load 会加载为 dict/OrderedDict
            m = torch.jit.load(path, map_location="cpu")
            # 如果能加载且类型是 ScriptModule / RecursiveScriptModule，即为 TorchScript
            return "ScriptModule" in type(m).__name__
        except Exception:
            return False

    def _detect_local_image(self):
        """对本地图片做单帧推理（单张/多次只有一张时）。

        推理源下拉选择「Nano 下位机模型」→ 走 TCP 下发位机推理（与批量一致）；
        否则走 PC 本地推理 core.local_infer.LocalInferEngine 后台线程
        （异步执行、信号回传、不阻塞 UI）。
        """
        # 确保不跟实时流同时跑
        if self.stream_engine.is_running:
            self.stream_engine.stop()
        # 下位机图片模式：板端读自己的图片 + 板端当前模型，走 nano_detect_request
        if getattr(self, "_nano_image_active", False):
            self._nano_start_detect()
            return
        path = self._local_image_path
        basename = os.path.basename(path)

        # ⭐ 推理源 = Nano 下位机 → 单张也走下位机推理（对齐批量逻辑）
        source = self.page_realtime.get_infer_source()
        if "Nano" in source:
            if not self.controller.tcp.is_connected:
                self.controller.log_message.emit(
                    "WARN", "检测", "推理源为 Nano 下位机但未连接，请先连接下位机")
                self.status_left.setText(f"本地图片: {basename}　| Nano 未连接")
                return
            self._nano_local_pending = path
            self.status_left.setText(f"本地图片: {basename}　| Nano 推理中...")
            self.controller.log_message.emit(
                "INFO", "检测", f"开始 Nano 下位机推理: {basename}")
            ok, buf = cv2.imencode(".jpg", self._local_image)
            if ok:
                self.controller.send_image(buf.tobytes(), path)
            else:
                self._nano_local_pending = ""
                self.controller.log_message.emit(
                    "ERROR", "检测", f"图片编码失败: {basename}")
            return

        # 没有本地模型
        if not self._local_model:
            self.controller.log_message.emit(
                "WARN", "检测", "未设置本地模型，无法检测本地图片")
            if QApplication.platformName() != "offscreen":
                QMessageBox.information(
                    self, "本地图片检测",
                    "当前未设置 PC 本地模型。\n\n"
                    "请先到「模型管理」或点击「加载模型」选择 .onnx / .pt 模型，\n"
                    "然后再进行本地图片检测。")
            return

        # 模型文件不存在
        if not os.path.isfile(self._local_model):
            self.controller.log_message.emit(
                "WARN", "检测", f"本地模型文件不存在: {self._local_model}")
            if QApplication.platformName() != "offscreen":
                QMessageBox.warning(
                    self, "本地图片检测",
                    f"模型文件不存在或已被删除:\n{self._local_model}\n\n"
                    "请重新选择本地模型。")
            return

        ext = os.path.splitext(self._local_model)[1].lower()
        # 格式不支持
        if ext not in (".onnx", ".pt"):
            self.controller.log_message.emit(
                "WARN", "检测",
                f"本地模型格式 {ext} 暂不支持，请使用 .onnx / .pt 模型")
            if QApplication.platformName() != "offscreen":
                QMessageBox.information(
                    self, "本地图片检测",
                    f"当前本地模型:\n{self._local_model}\n\n"
                    f"格式 {ext} 暂不支持 PC 本地推理。\n"
                    "请加载 .onnx 或 .pt 模型后再检测。")
            return

        # .pt 先排除 TorchScript 模型（文件名含 torchscript 或加载后类型不符）
        if self._is_torchscript_model(self._local_model):
            self.controller.log_message.emit(
                "WARN", "检测",
                f"TorchScript 模型暂不支持本地推理: {os.path.basename(self._local_model)}")
            if QApplication.platformName() != "offscreen":
                QMessageBox.warning(
                    self, "本地图片检测",
                    f"当前模型是 TorchScript 格式：\n{self._local_model}\n\n"
                    "该格式无法直接用于 PC 本地推理。\n\n"
                    "请使用以下方式之一解决：\n"
                    "1. 换成标准的 PyTorch 训练权重 (.pt)\n"
                    "2. 导出为 ONNX 格式 (.onnx) 后加载\n\n"
                    "例如：python export.py --weights yolov5s.pt --include onnx")
            return

        # 复用同一个推理引擎（懒加载），结果/错误经信号回主线程
        if getattr(self, "_local_infer_engine", None) is None:
            from core.local_infer import LocalInferEngine
            eng = LocalInferEngine(self)
            eng.result_ready.connect(self._on_local_infer_result)
            eng.error_ready.connect(self._on_local_infer_error)
            self._local_infer_engine = eng

        if self._local_infer_engine.busy:
            self.controller.log_message.emit(
                "WARN", "检测", "本地推理正在进行，请稍候")
            return

        # 启动后台推理（不阻塞 UI），状态先行提示
        self.status_left.setText(f"本地图片: {basename}　|　推理中...")
        self.controller.log_message.emit(
            "INFO", "检测", f"开始本地推理: {basename}（模型 {os.path.basename(self._local_model)}）")
        self._local_infer_engine.detect(path, self._local_model,
                                        conf_thres=0.25, iou_thres=0.45)

    def _on_local_infer_result(self, result: dict):
        """本地推理完成（后台线程回传）→ 转 UI 格式 → 统一入管线（KPI/写库/历史/预览画框）"""
        # 用户已点击停止，忽略延迟到达的推理结果
        if getattr(self, "_detection_paused", False):
            return
        from core.class_names import resolve_class_names, class_name_of
        class_names = resolve_class_names(self._local_model)
        raw = result.get("detections", [])
        dets = []
        for d in raw:
            box = d.get("box", [0, 0, 0, 0])
            cid = d.get("class_id", 0)
            cls = class_name_of(class_names, cid)
            dets.append((cls, d.get("confidence", 0), *box[:4]))
        ms = result.get("timing", {}).get("total_ms", 0) or 0
        path = self._local_image_path or ""
        basename = os.path.basename(path) or "本地图片"

        # 再次检查：如果停止按钮已触发，则只记录不渲染
        if getattr(self, "_detection_paused", False):
            self.controller.log_message.emit(
                "INFO", "检测", f"本地推理完成但已停止，忽略结果: {basename}")
            self.status_left.setText(
                f"本地图片: {basename}　| 已停止")
            return

        self.controller.ingest_result(dets, self._local_image, path)
        self._multi_results[path] = {"dets": list(dets), "ms": ms}
        self.controller.log_message.emit(
            "INFO", "检测",
            f"本地图片检测完成: {basename} "
            f"({'NG 缺陷×' + str(len(dets)) if dets else 'OK'})  耗时 {ms:.0f} ms")
        self.status_left.setText(
            f"本地图片: {basename}　| 检测完成 {ms:.0f} ms")
        if self._batch_running:
            self.page_realtime.update_batch_progress(
                len(self._multi_results), len(self._multi_image_paths))
            self._batch_next()

    def _on_local_infer_error(self, msg: str):
        """本地推理失败（后台线程回传）"""
        self.controller.log_message.emit("ERROR", "检测", f"本地推理失败: {msg}")
        self.page_realtime.set_running(False)
        self.status_left.setText(
            f"本地图片: {os.path.basename(self._local_image_path) or '--'}　| 推理失败")
        if QApplication.platformName() != "offscreen":
            QMessageBox.warning(
                self, "本地图片检测",
                f"模型推理时出错:\n{msg}\n\n"
                "请检查模型文件是否完整，或尝试其他 .onnx / .pt 模型。")

    def _on_stop(self, cancel_batch: bool = True):
        """停止实时流；保留本地图片模式，方便用户再次点击开始检测同一图片。
        cancel_batch=False 仅供内部加载图片时清框使用（不清空批量队列）。"""
        # 标记停止：后续延迟到达的推理结果不再渲染，避免停止后框又出现
        self._detection_paused = True
        # 用户主动停止 → 取消未完成的批量检测：清队列/清 Nano 挂起请求，
        # 避免延迟结果继续翻页或画框
        if cancel_batch:
            was_batch = (getattr(self, "_batch_running", False)
                         or bool(self._batch_queue) or bool(self._nano_local_pending)
                         or bool(getattr(self, "_nano_file_pending", "")))
            self._batch_running = False
            self._batch_queue = []
            self._nano_local_pending = ""
            self._nano_file_pending = ""
            self._nano_wait_preview_detect = ""
            self._nano_start_pending = False
            if was_batch:
                done = len(self._multi_results)
                total = len(self._multi_image_paths)
                self.page_realtime.lbl_batch_prog.setVisible(True)
                self.page_realtime.lbl_batch_prog.setText(
                    f"批量检测已停止（{done}/{total} 完成）")
                self.page_realtime.lbl_batch_prog.setStyleSheet(
                    "color:#f59e0b; font-size:13px; background:transparent;")
                self.page_realtime.bar_batch.setVisible(False)
        # 产线模拟流：发 stop 指令（保留订阅，可重启）
        if getattr(self, "_stream_camera_active", False):
            try:
                self.controller.tcp.control_stream("stop")
            except Exception:
                pass
            self._stream_camera_active = False
            self.controller.log_message.emit("INFO", "检测", "产线模拟已停止")

        if self.stream_engine.is_running:
            self.stream_engine.stop()
        eng = getattr(self, "_local_infer_engine", None)
        if eng is not None and getattr(eng, "busy", False):
            try:
                eng.cancel()
            except Exception:
                pass
        self.page_realtime.set_running(False)

        # 清除预览图上的检测框与 NG 浮窗
        # 多次清空 + 强制立即重绘 + 延迟安全网，防止任何竞态导致残留
        try:
            self.page_realtime.preview.clear_detections()
            self.page_realtime.preview.repaint()
        except Exception:
            pass
        self.page_realtime.update_detections([])
        try:
            self.page_realtime.preview.clear_detections()
            self.page_realtime.preview.repaint()
        except Exception:
            pass

        # 清空右侧当前结果详情，让停止有明确视觉反馈（KPI 累计值保留）
        self.page_realtime.update_detail(
            "Product_A_v1", "--", "--", 0, "--",
            time.strftime("%Y-%m-%d %H:%M:%S"),
            self._local_image_path or "--")

        # 本地图片/下位机图片模式下重新显示原图，确保框被彻底清除
        if (self._local_image_active or getattr(self, "_nano_image_active", False)) \
                and self._local_image is not None:
            rgb = cv2.cvtColor(self._local_image, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            self.page_realtime.update_image(
                QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy())
            tag = "本地图片" if self._local_image_active else "下位机图片"
            self.status_left.setText(
                f"{tag}: {os.path.basename(self._local_image_path)}　|　已停止，可再次检测")

        # 延迟安全网：再清两次，捕获任何延迟到达的信号
        QTimer.singleShot(50, self._safe_clear_detections)
        QTimer.singleShot(150, self._safe_clear_detections)

        self.controller.log_message.emit("INFO", "检测", "检测已停止")

    def _safe_clear_detections(self):
        """停止检测后的安全清框：_detection_paused 期间任何延迟结果都会触发这里"""
        if getattr(self, "_detection_paused", False):
            try:
                self.page_realtime.preview.clear_detections()
                self.page_realtime.preview.repaint()
            except Exception:
                pass

    def _on_stream_frame(self, frame):
        self._last_frame = frame

    def _on_fps(self, fps):
        self.status_left.setText(f"运行中　|　检测帧率: {fps:.0f} FPS")
        self.page_log.set_fps(fps)

    def _on_detection_result(self, result: dict):
        # 用户已点击停止，忽略延迟到达的结果，避免框继续显示
        if getattr(self, "_detection_paused", False):
            # 强制清空预览框，避免停止前最后一帧残留
            try:
                self.page_realtime.preview.clear_detections()
                self.page_realtime.preview.repaint()
                self.controller.log_message.emit(
                    "DEBUG", "检测", "忽略停止后的延迟检测结果并清框")
            except Exception:
                pass
            return

        dets = result.get("detections", [])
        frame = result.get("frame")
        if frame is None:
            frame = self._last_frame
        path = result.get("image_path", "")
        verdict = "NG" if dets else "OK"
        ts = time.strftime("%Y-%m-%d %H:%M:%S")

        # 预览 + 画框
        if frame is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            self.page_realtime.update_image(
                QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy())
        self.page_realtime.update_detections(dets)

        # 右侧详情 + KPI
        first = dets[0] if dets else None
        if dets:
            # 汇总缺陷类型及数量：inclusion ×1, scratches ×2
            from collections import Counter
            cnt = Counter(d[0] for d in dets)
            type_str = ", ".join(f"{cls} ×{n}" for cls, n in cnt.items())
            # 取面积最大的缺陷框作为代表（面积/置信度）
            biggest = max(dets, key=lambda d: abs((d[4] - d[2]) * (d[5] - d[3])))
            area = int(abs((biggest[4] - biggest[2]) * (biggest[5] - biggest[3])))
            conf = f"{biggest[1]:.2f}"
        else:
            type_str, area, conf = "-", 0, "--"
        self.page_realtime.update_detail(
            "Product_A_v1", verdict, type_str,
            area, conf, ts,
            path or "C:/data/images/sample.png")
        st = self.controller.get_stats()
        yld = st["pass"] / st["total"] * 100 if st["total"] else 0
        self.page_realtime.update_kpi(st["total"], st["pass"], st["defect"], yld)

        # 底部历史
        self.page_realtime.add_history([
            time.strftime("%H:%M:%S"), "Product_A_v1", verdict,
            first[0] if first else "-", os.path.basename(path) if path else "--"])

        # NG 归档
        if dets and frame is not None:
            fid = int(time.time() * 1000) % 100000
            self.ng_saver.enqueue_save(frame, dets, fid)
            self.ng_saver.enqueue_low_conf(frame, dets, fid)

        # 通信页 I/O 状态
        self.page_comm.update_io_state(self._plc_running, False, verdict)

        # 日志
        if dets:
            self.controller.log_message.emit(
                "ERROR", "检测",
                f"检测到缺陷：{first[0]}，置信度 {first[1]:.2f}")
        else:
            self.controller.log_message.emit("INFO", "检测", "检测通过")

        # Nano 本地图片检测结果：存结果并继续下一张（批量）或更新状态（单张）
        if self._nano_local_pending:
            path = self._nano_local_pending
            self._nano_local_pending = ""
            ms = 0
            self._multi_results[path] = {"dets": list(dets), "ms": ms}
            if self._batch_running:
                self.page_realtime.update_batch_progress(
                    len(self._multi_results), len(self._multi_image_paths))
                self._batch_next()
            else:
                self.status_left.setText(
                    f"本地图片: {os.path.basename(path)}　| Nano 检测完成")
        # 下位机图片检测结果：存结果并继续下一张（批量）或更新状态（单张）
        if self._nano_file_pending:
            path = self._nano_file_pending
            self._nano_file_pending = ""
            self._multi_results[path] = {"dets": list(dets), "ms": 0}
            if self._batch_running:
                self.page_realtime.update_batch_progress(
                    len(self._multi_results), len(self._multi_image_paths))
                self._batch_next()
            else:
                self.status_left.setText(
                    f"下位机图片: {os.path.basename(path)}　| Nano 检测完成")

    def _on_ng_alarm(self, count):
        self.controller.log_message.emit(
            "ERROR", "检测", f"连续 {count} 次检出缺陷，请检查产线状态！")
        # 微信告警推送（企业微信/Server酱，配置 data/alarm_config.json）
        try:
            from core.alarm import get_pusher
            st = self.controller.get_stats()
            msg = (f"⚠️ 缺陷检测告警\n"
                   f"连续 {count} 次检出缺陷\n"
                   f"当前累计: 总 {st['total']} / 缺陷 {st['defect']} / 良率 "
                   f"{st['pass']/st['total']*100:.1f}%\n"
                   f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            if get_pusher().push(msg):
                self.controller.log_message.emit("INFO", "告警", "微信告警推送已受理（后台发送中）")
        except Exception as e:
            self.controller.log_message.emit("WARN", "告警", f"微信告警失败: {e}")
        if QApplication.platformName() == "offscreen":
            return  # 无头模式不弹窗

        # 避免弹窗堆叠：复用同一个非模态告警框，5 分钟内不重复新建
        now = time.time()
        cooldown = getattr(self, "_ng_alarm_cooldown", 0)
        if now < cooldown:
            return

        box = getattr(self, "_ng_alarm_box", None)
        if box is None:
            box = QMessageBox(self)
            box.setWindowTitle("连续 NG 告警")
            box.setIcon(QMessageBox.Warning)
            box.setWindowModality(Qt.NonModal)
            box.setStandardButtons(QMessageBox.Ok)
            box.finished.connect(lambda _: setattr(self, "_ng_alarm_box", None))
            self._ng_alarm_box = box

        box.setText(f"连续检出 {count} 个缺陷！")
        box.setInformativeText("可能原因：产线异常 / 相机失焦 / 光源异常，请立即检查。")
        box.show()
        box.raise_()
        box.activateWindow()
        self._ng_alarm_cooldown = now + 300  # 5 分钟冷却

    # ================= 配置/ROI =================
    def _sync_cfg_to_realtime(self):
        cam = self.cfg.get("camera", {})
        det = self.cfg.get("detect", {})
        st = self.cfg.get("storage", {})
        p = self.page_realtime
        p.combo_camera.setCurrentText(cam.get("name", "相机 01"))
        p.spin_exposure.setValue(cam.get("exposure", 10.0))
        p.spin_gain.setValue(cam.get("gain", 2.0))
        p.spin_bright.setValue(cam.get("brightness", 128))
        p.spin_conf.setValue(det.get("conf", 0.85))
        p.spin_area.setValue(det.get("min_area", 50))
        p.edit_save_path.setText(st.get("save_path", "D:/Inspect/Images"))
        p.combo_clean.setCurrentText(st.get("auto_clean", "磁盘空间 < 10% 时删除"))

    def _on_roi_changed(self, rois):
        self._rois = rois
        # 两页互相同步（避免回环：仅更新不同步的一方）
        sender = self.sender()
        if sender is not self.page_realtime:
            self.page_realtime.set_rois(rois)
        if sender is not self.page_param:
            self.page_param.set_rois(rois)
        # 持久化 ROI 变更
        self.cfg["rois"] = rois
        cfg_mod.save_config(self.cfg)

    def _on_save_config(self, cfg: dict):
        merged = dict(self.cfg)
        merged.update(cfg)
        self.cfg = merged
        cfg_mod.save_config(merged)
        self.ng_saver.set_save_root(
            merged.get("storage", {}).get("save_path", "D:/Inspect/Images"))
        self.controller.log_message.emit("INFO", "系统", "配置已保存")
        _popup_info(self, "保存成功", "参数配置已保存，重启后仍会生效。")

    def _on_reset_config(self):
        self.cfg = _deep_default()
        self.page_param.apply_config(self.cfg)
        self._sync_cfg_to_realtime()
        self.page_realtime.set_rois(self.cfg["rois"])
        self.page_param.set_rois(self.cfg["rois"])
        self.controller.log_message.emit("INFO", "系统", "已恢复默认配置")
        _popup_info(self, "已恢复默认", "所有参数已恢复为默认值。")

    def _on_save_path_changed(self, path: str):
        self.cfg.setdefault("storage", {})["save_path"] = path
        cfg_mod.save_config(self.cfg)
        self.ng_saver.set_save_root(path)
        self.page_param.apply_config(self.cfg)
        self.controller.log_message.emit("INFO", "系统", f"保存路径已更新: {path}")
        _popup_info(self, "已设置", f"图像保存路径已更新：\n{path}")

    def _on_params_apply(self, conf, iou):
        rois = [{"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]}
                for r in self._rois if r.get("enabled", True)]
        if self.controller.tcp.is_connected:
            self.controller.tcp.send_control(conf_thres=conf, iou_thres=iou, rois=rois)
            self.controller.log_message.emit(
                "INFO", "参数", f"已下发参数: conf={conf}, roi×{len(rois)}")
            _popup_info(self, "应用成功",
                        f"检测参数已下发到下位机：\n置信度 {conf}，ROI {len(rois)} 个")
        else:
            self.controller.log_message.emit("INFO", "参数",
                                             f"参数已应用（本地）: conf={conf}")
            _popup_info(self, "应用成功",
                        f"检测参数已应用（未连接下位机，仅本地生效）：\n置信度 {conf}")

    def _on_conf_changed(self, conf: float):
        """实时页置信度数值变化 → 即时下发（无需点应用，检测中直接生效）"""
        try:
            if self.controller.tcp.is_connected:
                rois = [{"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]}
                        for r in self._rois if r.get("enabled", True)]
                self.controller.tcp.send_control(conf_thres=float(conf), rois=rois)
            else:
                self.stream_engine.set_local_conf(float(conf))
            self.cfg.setdefault("detect", {})["conf"] = float(conf)
            self.controller.log_message.emit(
                "DEBUG", "参数", f"置信度实时更新: {conf:.2f}")
        except Exception as e:
            self._log_tcp_debug("WARN", f"实时置信度下发失败: {e}")

    # ================= 模型 =================
    def _on_load_model(self, name=""):
        """「加载模型」入口：打开模型选择对话框，默认显示「选择模型」Tab"""
        from components.model_manager_dialog import ModelManagerDialog
        dlg = ModelManagerDialog(
            self.controller.tcp, self,
            default_model=self._local_model,
            default_model_dir=self.cfg.get("state", {}).get("last_model_dir", ""),
            open_tab="select"
        )
        dlg.local_model_selected.connect(self._on_local_model_selected)
        dlg.local_model_deleted.connect(self._on_local_model_deleted)
        dlg.reconnect_requested.connect(self._on_model_mgr_reconnect)
        dlg.exec_()

    def _on_model_mgr(self):
        """「模型管理」入口：打开模型管理对话框，默认显示「模型管理」Tab"""
        from components.model_manager_dialog import ModelManagerDialog
        dlg = ModelManagerDialog(
            self.controller.tcp, self,
            default_model=self._local_model,
            default_model_dir=self.cfg.get("state", {}).get("last_model_dir", ""),
            open_tab="manage"
        )
        dlg.local_model_selected.connect(self._on_local_model_selected)
        dlg.local_model_deleted.connect(self._on_local_model_deleted)
        dlg.reconnect_requested.connect(self._on_model_mgr_reconnect)
        dlg.exec_()

    def _on_local_model_deleted(self, path: str):
        """模型管理对话框中删除了本地模型文件/条目：若正是当前本地模型则清空"""
        if path and self._local_model == path:
            self._local_model = ""
            self._save_state(last_local_model="")
            self.title_bar.set_model_tag("--")
            self.controller.log_message.emit(
                "WARN", "模型", "当前本地模型已被删除，请重新选择模型")
        else:
            self.controller.log_message.emit(
                "INFO", "模型", f"已从模型库移除: {os.path.basename(path)}")

    def _on_models_updated(self, data):
        """下位机模型清单变化 → 同步标题栏/页面当前模型"""
        active = data.get("active", "")
        if not active:
            return
        base = os.path.basename(active)
        self.title_bar.set_model_tag(base)
        self.page_realtime.set_cur_model(base)
        self.page_param.set_cur_model(base)
        self.controller.log_message.emit("INFO", "模型", f"下位机当前模型: {base}")
        # 记忆 Nano 当前模型 + 同步类别映射
        self._save_state(last_nano_model=active)
        self.controller.nano_model_name = active

    def _on_model_load_result(self, payload):
        """下位机模型切换结果 → 同步标题栏/页面"""
        if not payload.get("ok"):
            self.controller.log_message.emit(
                "WARN", "模型", f"切换失败: {payload.get('error', '')}")
            return
        model = payload.get("model", "")
        base = os.path.basename(model)
        self.title_bar.set_model_tag(base)
        self.page_realtime.set_cur_model(base)
        self.page_param.set_cur_model(base)
        self.controller.log_message.emit("INFO", "模型", f"模型切换成功: {base}")
        # 记忆 Nano 当前模型 + 同步类别映射
        self._save_state(last_nano_model=model)
        self.controller.nano_model_name = model

    def _on_nano_connected(self):
        """检测到开发板通信 → 自动切换为开发板模型"""
        self._nano_active = True
        nano_model = self.cfg.get("state", {}).get("last_nano_model", "")
        base = os.path.basename(nano_model) if nano_model else "Nano 模型"
        self.title_bar.set_model_tag(base)
        self.page_realtime.set_cur_model(base)
        self.page_param.set_cur_model(base)
        self.controller.log_message.emit(
            "INFO", "模型", f"检测到开发板通信，已切换为开发板模型: {base}")
        self.status_left.setText(f"开发板已连接　|　模型: {base}")
        # 启动 Nano 状态轮询
        if not getattr(self, "_status_timer", None):
            self._status_timer = QTimer(self)
            self._status_timer.setInterval(5000)
            self._status_timer.timeout.connect(self._poll_nano_status)
        self._status_timer.start()
        self._poll_nano_status()
        # 连接开发板后默认切换为 Nano 下位机检测 + 拉取清单同步激活模型
        self.page_realtime.set_infer_source("Nano 下位机模型")
        self.model_ctl.refresh()
        self.page_log.set_nano_online(True)
        # 连接成功弹窗（10 秒内不重复，避免自动重连风暴频繁弹窗）
        now = time.time()
        if now - getattr(self, "_last_conn_popup_ts", 0) >= 10:
            self._last_conn_popup_ts = now
            if QApplication.platformName() != "offscreen":
                tcp = self.controller.tcp
                QMessageBox.information(
                    self, "连接成功",
                    f"已成功连接下位机（{getattr(tcp, 'host', '')}:{getattr(tcp, 'port', '')}），"
                    f"当前模型：{base}")

    def _on_nano_disconnected(self):
        """开发板断开 → 自动切回本地模型"""
        self._nano_active = False
        if self._local_model and os.path.isfile(self._local_model):
            base = os.path.basename(self._local_model)
            self.title_bar.set_model_tag(base)
            self.page_realtime.set_cur_model(base)
            self.page_param.set_cur_model(base)
            self.controller.log_message.emit(
                "INFO", "模型", f"开发板已断开，切回本地模型: {base}")
        else:
            self.title_bar.set_model_tag("--")
            self.controller.log_message.emit("INFO", "模型", "开发板已断开，无本地模型")
        if not self.stream_engine.is_running:
            self.status_left.setText("就绪　|　检测帧率: -- FPS")
        # 停止 Nano 状态轮询
        if getattr(self, "_status_timer", None):
            self._status_timer.stop()
        # 断开后切回 PC 本地检测
        self.page_realtime.set_infer_source("PC 本地模型")
        self.page_log.set_nano_online(False)

    # ---------------- Nano 状态轮询 ----------------
    def _poll_nano_status(self):
        """周期请求下位机系统状态（仅已连接时）"""
        if self.controller.tcp.is_connected:
            self.controller.tcp.request_status()

    def _on_nano_status(self, status: dict):
        """status_response → 状态栏显示 Nano GPU/CPU/内存/温度/推理耗时"""
        try:
            gpu = status.get("gpu_util")
            cpu = status.get("cpu_util")
            mem = status.get("mem_used_gb")
            temp = status.get("gpu_temp")
            ms = status.get("last_detect_ms")
            parts = []
            parts.append(f"GPU {gpu}%" if gpu is not None else "GPU --")
            parts.append(f"CPU {cpu}%" if cpu is not None else "CPU --")
            if mem is not None:
                parts.append(f"内存 {mem:.1f}G")
            if temp is not None:
                parts.append(f"{temp:.0f}°C")
            if ms is not None:
                parts.append(f"推理 {ms:.0f}ms")
            model = status.get("model")
            suffix = f" | Nano: {model}" if model else ""
            self.status_left.setText(" | ".join(parts) + suffix)
            self.page_log.update_nano_status(status)
        except Exception as e:
            self._log_tcp_debug("WARN", f"状态显示异常: {e}")

    def _on_local_model_selected(self, path):
        import os
        self._local_model = path
        self.controller.log_message.emit(
            "INFO", "模型", f"已选择本地模型 {os.path.basename(path)}，开始检测时启用本地推理")
        self.title_bar.set_model_tag(os.path.basename(path))
        self.page_param.set_cur_model(os.path.basename(path))
        self.page_realtime.set_cur_model(os.path.basename(path))
        # 持久化
        self._save_state(last_local_model=path,
                         last_model_dir=os.path.dirname(path) or "")

    def _save_state(self, **kwargs):
        """更新并保存 ui_state 到配置文件"""
        state = dict(self.cfg.get("state", {}))
        state.update(kwargs)
        self.cfg["state"] = state
        cfg_mod.save_config(self.cfg)

    # ================= 历史 =================
    def _on_history_query(self, filters, limit, offset):
        db = self.controller.db
        rows = db.query_records(limit=limit, offset=offset, **filters)
        total = db.count_records(**filters)
        kpi = db.get_kpi(**filters)
        self.page_history.set_defect_types(db.get_defect_types())
        self.page_history.render(rows, total, kpi)
        # A4 统计图表刷新
        try:
            daily = db.get_daily_stats(days=14)
            types = db.get_defect_type_stats()
            self.page_history.update_charts(daily, types)
        except Exception as e:
            self.controller.log_message.emit("WARN", "历史", f"图表刷新失败: {e}")

    def _on_history_export(self, filters):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出记录", "defect_records.csv", "CSV (*.csv)")
        if not path:
            return
        n = self.controller.db.export_csv(path, **filters)
        self.controller.log_message.emit("INFO", "历史", f"已导出 {n} 条到 {path}")
        _popup_info(self, "导出成功", f"已导出 {n} 条检测记录到：\n{path}")

    def _on_history_report(self, filters):
        """U9 检测报告导出（NG 拼图 + 统计，HTML）"""
        default_name = f"defect_report_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出检测报告", default_name, "HTML 报告 (*.html)")
        if not path:
            return
        try:
            report = self.controller.db.export_report(path, **filters)
            if report["count"] == 0:
                _popup_info(self, "无数据", "当前筛选条件下没有检测记录")
                return
            self.controller.log_message.emit(
                "INFO", "历史",
                f"已导出报告: {report['count']} 条 (NG {report['ng']}, "
                f"良率 {report['yield']}%)")
            _popup_info(self, "导出成功",
                        f"检测报告已导出：\n{path}\n\n"
                        f"记录 {report['count']} 条 · NG {report['ng']} · "
                        f"良率 {report['yield']}%")
        except Exception as e:
            self.controller.log_message.emit("ERROR", "历史", f"报告导出失败: {e}")
            _popup_info(self, "导出失败", f"报告导出失败：\n{e}")

    def _on_history_clear(self):
        """清空所有检测记录与生产统计"""
        try:
            n = self.controller.db.clear_all_records()
            self.controller.log_message.emit("INFO", "历史", f"已清空 {n} 条记录")
            _popup_info(self, "清空成功", "所有检测记录已清空。")
            self.page_history._query(1)
        except Exception as e:
            self.controller.log_message.emit("ERROR", "历史", f"清空失败: {e}")
            _popup_info(self, "清空失败", f"清空记录时出错：\n{e}")

    # ================= 通信 =================
    def _on_plc_status(self, status):
        self._plc_running = status.get("running", False)
        self.page_comm.update_io_state(
            self._plc_running, status.get("fault", False))
        s = self.controller.plc.get_stats()
        self.page_comm.update_stats(
            s["tx"], s["rx"], s["err"], status.get("rtt_ms", 0))

    def _on_test_comm(self):
        plc = self.controller.plc
        if plc.is_connected:
            try:
                st = plc.read_status()
                rtt = getattr(plc, '_last_rtt', 0)
                self.controller.log_message.emit(
                    "INFO", "PLC", f"测试通信成功 RTT={rtt:.1f}ms")
                self.page_comm.append_txrx(
                    f"[TX] 01 03 00 00 00 01　→　[RX] OK running={st['running']}")
                _popup_info(self, "通信正常",
                            f"PLC 通信测试成功，往返延迟 {rtt:.1f} ms")
            except Exception as e:
                self.controller.log_message.emit("ERROR", "PLC", f"测试通信失败: {e}")
                _popup_info(self, "通信失败", f"PLC 通信测试失败：\n{e}")
        else:
            self.controller.log_message.emit("WARN", "PLC", "PLC 未连接，无法测试")
            _popup_info(self, "无法测试", "PLC 未连接，请先在通信设置页连接 PLC。")

    def _on_tcp_reconnect(self):
        """手动重新连接下位机推理服务（断开旧线程后立即重连）"""
        self.controller.tcp.disconnect()
        t = self.cfg.get("tcp", {})
        host = t.get("host", "192.168.1.101")
        port = t.get("port", 8888)
        self.controller.configure_tcp(
            host, port,
            heartbeat=t.get("heartbeat", 5), retries=t.get("retries", 3),
            timeout=t.get("timeout", 10))
        self.controller.connect_tcp()
        self.controller.log_message.emit(
            "INFO", "通信", f"正在重新连接下位机 {host}:{port}，结果请关注右下角运行日志")

    def _on_model_mgr_reconnect(self):
        """模型管理对话框里的重新连接按钮"""
        self._on_tcp_reconnect()

    def _log_tcp_debug(self, level, msg):
        """把 TCP 相关日志追加到文件，便于 pythonw 无控制台时排查"""
        import os, time
        try:
            path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "data", "tcp_debug.log")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} [{level}] {msg}\n")
        except Exception:
            pass

    def _on_status_changed(self, name, status):
        bar = {"camera": self.title_bar.light_camera,
               "plc": self.title_bar.light_plc,
               "model": self.title_bar.light_model}.get(name)
        if bar:
            bar.set_status(status)
        if name == "plc":
            self.page_comm.set_plc_conn_ui(status == 1)
            self.page_realtime.set_plc_light(status == 1)
        if name == "model":
            # 同步实时页下位机状态灯
            self.page_realtime.light_nano_mini.set_status(
                status, "已连接" if status == 1 else "未连接")
            if status == 1:
                self.controller.tcp.request_model_list()

    # ================= 杂项 =================
    def _on_save_image(self, path):
        if self._last_frame is not None:
            cv2.imwrite(path, self._last_frame)
            self.controller.log_message.emit("INFO", "系统", f"图像已保存: {path}")
            _popup_info(self, "保存成功", f"图像已保存到：\n{path}")
        else:
            self.controller.log_message.emit("WARN", "系统", "无可用帧，保存失败")
            _popup_info(self, "保存失败", "当前没有可保存的图像帧。\n\n"
                        "请先加载本地图片或开始实时检测后再保存。")

    def _tick_clock(self):
        self.status_time.setText(
            f"当前时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    def _on_menu(self):
        from PyQt5.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #1e293b; color: #e2e8f0; border: 1px solid #334155;"
            " font-size: 15px; padding: 4px; }"
            "QMenu::item { padding: 8px 24px; border-radius: 4px; }"
            "QMenu::item:selected { background-color: #2563eb; color: #ffffff; }")
        act_nano = menu.addAction("下位机图片检测")
        act_model = menu.addAction("模型管理")
        menu.addSeparator()
        act_about = menu.addAction("关于")
        act = menu.exec_(self.title_bar.mapToGlobal(self._menu_pos()))
        if act == act_nano:
            self._on_nano_image_detect()
        elif act == act_model:
            self._on_model_mgr()
        elif act == act_about:
            QMessageBox.about(self, "关于",
                              "工业缺陷检测上位机 v1.2\n"
                              "下位机: RK3568 / Jetson 系列\n"
                              "通信: TCP (4字节长度头 + JSON) / Modbus TCP / 串口\n"
                              "本地检测: ONNX / PT 模型（不再使用模拟演示）")

    def _menu_pos(self):
        """菜单弹出位置：标题栏菜单按钮附近"""
        return self.title_bar.rect().topRight() - self.title_bar.rect().topLeft() \
            + self.title_bar.pos()

    # ================= 下位机图片检测（2026-08-24，与本地图片同交互） =================
    def _nano_path_of(self, name: str) -> str:
        return f"nano://{self._nano_image_dir}/{name}"

    @staticmethod
    def _nano_name_of(path: str) -> str:
        return os.path.basename(str(path or "").replace("\\", "/"))

    def _nano_load_preview_by_name(self, name: str):
        """拉取下位机图片预览大图（640px）显示到主界面预览区"""
        self.status_left.setText(f"下位机图片: {name}　| 加载预览中...")
        self.controller.tcp.request_nano_image(name, 640)

    def _nano_load_preview(self, path: str):
        """按 nano:// 路径加载预览（对齐 _load_image_file 的入口语义）"""
        name = self._nano_name_of(path)
        if path in self._multi_image_paths:
            self._nano_image_idx = self._multi_image_paths.index(path)
        self._nano_load_preview_by_name(name)

    def _nano_start_detect(self):
        """单帧检测：对当前浏览的下位机图片发起检测"""
        if not self._nano_image_names:
            return
        idx = max(0, min(self._nano_image_idx, len(self._nano_image_names) - 1))
        self._nano_start_detect_for(self._nano_image_names[idx])

    def _nano_start_detect_for(self, name: str):
        """发起下位机图片检测：预览帧已在手直接发；否则先拉预览，就绪后自动发"""
        if self._nano_preview_name == name and self._local_image is not None:
            self.controller.tcp.request_nano_detect(name, annotate=False)
            return
        self._nano_wait_preview_detect = name
        self._nano_load_preview_by_name(name)

    def _batch_next_nano(self, path: str):
        """批量流程：下位机图片走 预览→检测 链路（不读本地文件）"""
        self._detection_paused = False
        self.page_realtime.update_nav(self._multi_image_idx, len(self._multi_image_paths))
        self.page_realtime.set_running(True)
        self.page_realtime.update_batch_progress(
            len(self._multi_results), len(self._multi_image_paths))
        name = self._nano_name_of(path)
        if not self.controller.tcp.is_connected:
            self._multi_results[path] = {"dets": [], "ms": 0}
            self._batch_next()
            return
        self.controller.log_message.emit(
            "INFO", "检测",
            f"下位机图片检测 [{self._multi_image_idx+1}/{len(self._multi_image_paths)}]: {name}")
        self.status_left.setText(
            f"批量检测 [{self._multi_image_idx+1}/{len(self._multi_image_paths)}] 下位机图片推理中...")
        # 先拉预览显示新图，预览就绪后自动续发检测（批量中逐张动态切换）
        self._nano_wait_preview_detect = name
        self._nano_load_preview_by_name(name)

    def _update_detail_for_path(self, path: str):
        """用 _multi_results 更新右侧详情表：缺陷类型汇总（同类显示 ×数量）+ 最大框信息"""
        r = self._multi_results.get(path, {})
        dets = r.get("dets", [])
        verdict = "NG" if dets else "OK"
        if dets:
            from collections import Counter
            cnt = Counter(d[0] for d in dets)
            type_str = ", ".join(f"{cls} ×{n}" for cls, n in cnt.items())
            biggest = max(dets, key=lambda d: abs((d[4] - d[2]) * (d[5] - d[3])))
            area = int(abs((biggest[4] - biggest[2]) * (biggest[5] - biggest[3])))
            conf = f"{biggest[1]:.2f}"
        else:
            type_str, area, conf = "-", 0, "--"
        self.page_realtime.update_detail(
            "Product_A_v1", verdict, type_str, area, conf,
            time.strftime("%Y-%m-%d %H:%M:%S"), path or "--")

    def _on_nano_preview_image(self, msg: dict):
        """下位机图片预览回传：显示原图（对齐本地图片加载）；等待检测时自动续发"""
        if not getattr(self, "_nano_image_active", False):
            return
        name = msg.get("name", "")
        if not msg.get("ok"):
            if self._nano_wait_preview_detect == name:
                self._nano_wait_preview_detect = ""
                path = self._nano_path_of(name)
                self._multi_results[path] = {"dets": [], "ms": 0,
                                             "error": msg.get("error", "")}
                self.status_left.setText(
                    f"下位机图片: {name}　| 预览失败 {msg.get('error', '')}")
                if self._batch_running:
                    self.page_realtime.update_batch_progress(
                        len(self._multi_results), len(self._multi_image_paths))
                    self._batch_next()
            else:
                self.status_left.setText(
                    f"下位机图片预览失败: {msg.get('error', '')}")
            return
        import base64
        import numpy as np
        raw = base64.b64decode(msg["image_b64"])
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return
        self._nano_preview_name = name
        self._local_image = img
        self._local_image_path = self._nano_path_of(name)
        self._local_image_active = True
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        self.page_realtime.update_image(
            QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy())
        path = self._local_image_path
        if path in self._multi_results:
            r = self._multi_results[path]
            self.page_realtime.update_detections(r.get("dets", []))
            self._update_detail_for_path(path)
            self.status_left.setText(
                f"下位机图片: {name}　| 检测完成 {r.get('ms', 0):.0f} ms")
        else:
            self.page_realtime.update_detections([])
            self.status_left.setText(f"下位机图片: {name}　| 点击开始检测")
        self.page_realtime.update_nav(self._nano_image_idx, len(self._multi_image_paths))
        # 预览就绪 → 自动续发等待中的检测
        if self._nano_wait_preview_detect == name:
            self._nano_wait_preview_detect = ""
            self.controller.tcp.request_nano_detect(name, annotate=False)
        # 开始检测时预览未就绪 → 就绪后自动重入 _on_start
        if getattr(self, "_nano_start_pending", False):
            self._nano_start_pending = False
            self._on_start()

    def _on_nano_detect_result(self, msg: dict):
        """下位机图片单张检测结果（nano_detect_response）：入管线 + 批量续接"""
        if getattr(self, "_detection_paused", False):
            return
        if not getattr(self, "_nano_image_active", False):
            return
        name = msg.get("name", "")
        path = self._nano_path_of(name)
        if not msg.get("ok"):
            self._multi_results[path] = {"dets": [], "ms": 0,
                                         "error": msg.get("error", "")}
            self.status_left.setText(f"下位机图片: {name}　| 检测失败 {msg.get('error', '')}")
            if self._batch_running:
                self.page_realtime.update_batch_progress(
                    len(self._multi_results), len(self._multi_image_paths))
                self._batch_next()
            return
        from core.class_names import resolve_class_names, class_name_of
        class_names = resolve_class_names(self.controller.nano_model_name)
        dets = []
        for d in msg.get("detections", []):
            box = d.get("box", [0, 0, 0, 0]) or [0, 0, 0, 0]
            b = []
            for v in box[:4]:
                try:
                    b.append(float(v))
                except (TypeError, ValueError):
                    b.append(0.0)
            while len(b) < 4:
                b.append(0.0)
            cid = d.get("class_id", 0)
            cls = class_name_of(class_names, cid)
            dets.append((cls, float(d.get("confidence", 0) or 0), *b))
        try:
            ms = float(msg.get("timing", {}).get("total_with_read", 0) or 0)
        except (TypeError, ValueError):
            ms = 0.0
        # 统一结果管线：预览/KPI/写库/历史/批量续接 由 _on_detection_result 完成
        self._nano_file_pending = path
        self.controller.ingest_result(dets, self._local_image, path)
        if not self._batch_running:
            self.status_left.setText(f"下位机图片: {name}　| 检测完成 {ms:.0f} ms")

    def _on_local_image(self):
        """本地图片：单次模式选一张，多次模式可选多张。
        选好后直接显示在实时预览区，不弹窗。"""
        # 切换到本地图片模式：退出下位机图片模式
        self._nano_image_active = False
        self._nano_image_names = []
        last_dir = self.cfg.get("state", {}).get("last_image_dir", "")
        start_dir = ""
        if self._local_model:
            from components.model_info import resolve_dataset_image_dir
            start_dir = resolve_dataset_image_dir(self._local_model)
        if not start_dir:
            start_dir = last_dir

        multi = self.page_realtime.get_detect_mode() == "多次检测"
        if multi:
            paths, _ = QFileDialog.getOpenFileNames(
                self, "选择检测图片（可多选）", start_dir,
                "图片文件 (*.png *.jpg *.jpeg *.bmp)")
            if not paths:
                return
            self._multi_image_paths = paths
            self._multi_image_idx = 0
            self._multi_results.clear()
            self._save_state(last_image_dir=os.path.dirname(paths[0]) or "")
            self._on_stop()
            self._local_image_active = True
            self._load_image_file(paths[0])
            self.page_realtime.update_nav(0, len(paths))
            source = self.page_realtime.get_infer_source()
            self.page_realtime.set_source(
                f"{'Nano 下位机' if 'Nano' in source else 'PC 本地'}（多图 {len(paths)} 张）", "#22d3ee")
            self.controller.log_message.emit("INFO", "检测", f"已选择 {len(paths)} 张图片，点击「开始检测」批量检测")
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择检测图片", start_dir,
                "图片文件 (*.png *.jpg *.jpeg *.bmp)")
            if not path:
                if self._local_image_active:
                    self._local_image = None
                    self._local_image_path = ""
                    self._local_image_active = False
                    self.page_realtime.set_source("--", "#94a3b8")
                    self.status_left.setText("就绪　|　检测帧率: -- FPS")
                return
            self._multi_image_paths = [path]
            self._multi_image_idx = 0
            self._multi_results.clear()
            self._save_state(last_image_dir=os.path.dirname(path) or "")
            self._on_stop()
            self._local_image_active = True
            self._load_image_file(path)
            source = self.page_realtime.get_infer_source()
            self.page_realtime.set_source(
                f"{'Nano 下位机' if 'Nano' in source else 'PC 本地'}（单图）", "#22d3ee")
            self.controller.log_message.emit("INFO", "检测", f"已加载本地图片: {os.path.basename(path)}")

    def _load_image_file(self, path: str):
        """加载图片文件到预览区（单图/多图共用）"""
        import numpy as np
        try:
            _raw = np.fromfile(path, dtype=np.uint8)
            _img = cv2.imdecode(_raw, cv2.IMREAD_COLOR)
        except Exception:
            _img = None
        if _img is None:
            self.controller.log_message.emit("ERROR", "检测", f"无法读取图片: {path}")
            return
        # 内部清框不取消批量（批量流程中 _batch_next 会调用本方法逐张加载）
        self._on_stop(cancel_batch=False)
        self._local_image = _img
        self._local_image_path = path
        self._local_image_active = True
        rgb = cv2.cvtColor(_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        self.page_realtime.update_image(
            QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy())
        self.page_realtime.update_detections([])
        basename = os.path.basename(path)
        self.status_left.setText(f"本地图片: {basename}　|　点击开始检测")

    def _start_batch(self):
        """启动批量检测：依次检测所有未检测的图片"""
        # 推理源为 Nano 但未连接 → 明确中止，绝不把图片静默记为 OK
        source = self.page_realtime.get_infer_source()
        if "Nano" in source and not self.controller.tcp.is_connected:
            self.controller.log_message.emit(
                "WARN", "检测",
                "推理源为 Nano 下位机但未连接，批量检测未启动。请先连接下位机或切换为 PC 本地模型")
            self.status_left.setText("批量检测未启动　|　Nano 未连接")
            self.page_realtime.set_running(False)
            return
        self._batch_running = True
        self._batch_queue = [p for p in self._multi_image_paths if p not in self._multi_results]
        total = len(self._multi_image_paths)
        done = len(self._multi_results)
        self.page_realtime.update_batch_progress(done, total)
        if not self._batch_queue:
            self._finish_batch()
            return
        self.controller.log_message.emit("INFO", "检测", f"开始批量检测 {len(self._batch_queue)} 张图片")
        self._batch_next()

    def _batch_next(self):
        """检测队列中下一张图片"""
        if not self._batch_queue:
            self._finish_batch()
            return
        path = self._batch_queue.pop(0)
        self._current_batch_path = path
        if path in self._multi_image_paths:
            self._multi_image_idx = self._multi_image_paths.index(path)
        # 下位机图片模式：不读本地文件，走 预览→检测 链路
        if getattr(self, "_nano_image_active", False):
            self._batch_next_nano(path)
            return
        self._load_image_file(path)
        # ⚠️ 关键：_load_image_file 内部调了 _on_stop() 会设 _detection_paused=True，
        # 必须在这里重置为 False，否则推理结果回来时会被忽略，批量检测卡住
        self._detection_paused = False
        self.page_realtime.update_nav(self._multi_image_idx, len(self._multi_image_paths))
        self.page_realtime.set_running(True)
        self.page_realtime.update_batch_progress(
            len(self._multi_results), len(self._multi_image_paths))

        source = self.page_realtime.get_infer_source()
        if "Nano" in source and self.controller.tcp.is_connected:
            # Nano 下位机推理（日志明确记录推理源，便于排查）
            self._nano_local_pending = path
            self.controller.log_message.emit(
                "INFO", "检测",
                f"Nano 下位机推理 [{self._multi_image_idx+1}/{len(self._multi_image_paths)}]: "
                f"{os.path.basename(path)}")
            self.status_left.setText(f"批量检测 [{self._multi_image_idx+1}/{len(self._multi_image_paths)}] Nano 推理中...")
            ok, buf = cv2.imencode(".jpg", self._local_image)
            if ok:
                self.controller.send_image(buf.tobytes(), path)
            else:
                self._multi_results[path] = {"dets": [], "ms": 0}
                self._batch_next()
        else:
            # PC 本地推理：如果模型无效会直接 return，需要检测是否真的启动了
            self._detect_local_image()
            # 如果推理引擎没有在忙，说明检测没启动（模型无效等），跳过这张
            eng = getattr(self, "_local_infer_engine", None)
            if eng is None or not eng.busy:
                # 如果是 Nano 模式但未连接，也跳过
                if "Nano" in source and not self.controller.tcp.is_connected:
                    self.controller.log_message.emit(
                        "WARN", "检测", "Nano 未连接，跳过此图片")
                self._multi_results[path] = {"dets": [], "ms": 0}
                self._batch_next()

    def _finish_batch(self):
        """批量检测完成：回到第一张，显示结果"""
        self._batch_running = False
        self._batch_queue = []
        self.page_realtime.set_running(False)
        done = len(self._multi_results)
        total = len(self._multi_image_paths)
        self.page_realtime.update_batch_progress(done, total)
        self.controller.log_message.emit("INFO", "检测", f"批量检测完成 {done}/{total} 张")
        if self._multi_image_paths:
            self._multi_image_idx = 0
            self._show_multi_image(0)
        self.status_left.setText(f"批量检测完成 {done}/{total} 张　|　显示第一张结果")

    def _show_multi_image(self, idx: int):
        """加载多图列表中的第 idx 张并显示检测结果（不清框、不触发停止）"""
        paths = self._multi_image_paths
        if idx < 0 or idx >= len(paths):
            return
        path = paths[idx]
        self._multi_image_idx = idx
        # 下位机图片模式：异步拉预览，回传时自动套用已存结果
        if getattr(self, "_nano_image_active", False):
            self._nano_image_idx = idx
            self._nano_load_preview(path)
            self.page_realtime.update_nav(idx, len(paths))
            return
        # 直接加载图片显示，不调 _load_image_file（它会触发 _on_stop 清框）
        import numpy as np
        try:
            _raw = np.fromfile(path, dtype=np.uint8)
            _img = cv2.imdecode(_raw, cv2.IMREAD_COLOR)
        except Exception:
            _img = None
        if _img is None:
            self.controller.log_message.emit("ERROR", "检测", f"无法读取图片: {path}")
            return
        self._local_image = _img
        self._local_image_path = path
        self._local_image_active = True
        # 显示原图
        rgb = cv2.cvtColor(_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        self.page_realtime.update_image(
            QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy())
        # 如果已有检测结果，显示框
        if path in self._multi_results:
            r = self._multi_results[path]
            dets = r.get("dets", [])
            self.page_realtime.update_detections(dets)
            self._update_detail_for_path(path)
            ms = r.get("ms", 0)
            self.status_left.setText(f"多图检测 [{idx+1}/{len(paths)}] {os.path.basename(path)}　| {ms:.0f}ms")
        else:
            self.page_realtime.update_detections([])
            self.status_left.setText(f"多图检测 [{idx+1}/{len(paths)}] {os.path.basename(path)}　| 未检测")
        self.page_realtime.update_nav(idx, len(paths))

    def _on_nav_prev(self):
        """多图导航：上一张（批量检测进行中不响应，避免与自动翻页竞争）"""
        if self._batch_running:
            return
        self._show_multi_image(self._multi_image_idx - 1)

    def _on_nav_next(self):
        """多图导航：下一张（批量检测进行中不响应，避免与自动翻页竞争）"""
        if self._batch_running:
            return
        self._show_multi_image(self._multi_image_idx + 1)

    def _on_batch_detect(self):
        """实时页「批量检测」：打开多图检测对话框，支持 PC 本地 / Nano 下位机"""
        from components.local_image_detect import LocalImageDetectDialog
        dlg = LocalImageDetectDialog(
            local_model=self._local_model,
            rois=[{"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]}
                  for r in self._rois if r.get("enabled", True)],
            parent=self,
            default_image_dir=self.cfg.get("state", {}).get("last_image_dir", ""),
            tcp_client=self.controller.tcp,
        )
        dlg.result_committed.connect(self._on_batch_result)
        dlg.exec_()

    # ================= 产线模拟流（2026-08-25） =================
    def _on_start_stream_camera(self):
        """开始产线模拟流：订阅 + start + FPS"""
        if not self.controller.tcp.is_connected:
            self.controller.log_message.emit(
                "WARN", "检测", "产线流需要连接 Nano，请先「重新连接」")
            if QApplication.platformName() != "offscreen":
                QMessageBox.information(
                    self, "产线模拟", "下位机未连接，请先「重新连接」。")
            return
        fps = self.page_realtime.spin_stream_fps.value()
        self._stream_camera_active = True
        self._detection_paused = False
        self.page_realtime.set_running(True)
        self.controller.tcp.subscribe_stream(True)
        self.controller.tcp.control_stream("start", fps=fps)
        self.controller.log_message.emit(
            "INFO", "检测", f"产线模拟启动: {fps} FPS，下位机自主检测中...")
        self.status_left.setText(f"产线模拟运行中　|　{fps} FPS　|　等待下位机推帧...")

    def _on_stream_frame(self, msg: dict):
        """产线流帧回传：显示标注图 → 入管线（KPI/NG/归档/历史）"""
        if getattr(self, "_detection_paused", False):
            return
        if not getattr(self, "_stream_camera_active", False):
            return
        import base64
        import numpy as np
        name = msg.get("name", "")
        seq = msg.get("seq", 0)
        dets_raw = msg.get("detections", [])
        # 标注图解码（已有框+标签，直接显示避免双框）
        frame = None
        if msg.get("annot_b64"):
            raw = base64.b64decode(msg["annot_b64"])
            frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        self.page_realtime.update_image(
            QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy())
        # detections → UI 格式
        from core.class_names import resolve_class_names, class_name_of
        class_names = resolve_class_names(self.controller.nano_model_name)
        dets = []
        for d in dets_raw:
            box = d.get("box", [0, 0, 0, 0]) or [0, 0, 0, 0]
            b = []
            for v in box[:4]:
                try:
                    b.append(float(v))
                except (TypeError, ValueError):
                    b.append(0.0)
            while len(b) < 4:
                b.append(0.0)
            cid = d.get("class_id", 0)
            cls = class_name_of(class_names, cid)
            dets.append((cls, float(d.get("confidence", 0) or 0), *b))
        self.page_realtime.update_detections(dets)
        # 详情表（Counter ×数量）
        path = f"stream://{name}"
        verdict = "NG" if dets else "OK"
        if dets:
            from collections import Counter
            cnt = Counter(d[0] for d in dets)
            type_str = ", ".join(f"{c} ×{n}" for c, n in cnt.items())
            biggest = max(dets, key=lambda dd: abs((dd[4]-dd[2])*(dd[5]-dd[3])))
            area = int(abs((biggest[4]-biggest[2])*(biggest[5]-biggest[3])))
            conf = f"{biggest[1]:.2f}"
        else:
            type_str, area, conf = "-", 0, "--"
        self.page_realtime.update_detail(
            "Product_A_v1", verdict, type_str, area, conf,
            time.strftime("%Y-%m-%d %H:%M:%S"), path)
        # 入管线（KPI/写库/NG 归档/PLC 剔除/历史）
        self.controller.ingest_result(dets, frame, path)
        ms = msg.get("timing", {}).get("total_with_read", 0)
        self.status_left.setText(
            f"产线流 #{seq} {name}　| {'NG ×'+str(len(dets)) if dets else 'OK'} {ms:.0f}ms")

    def _on_stream_control(self, msg: dict):
        """产线流控制/订阅/状态响应"""
        if msg.get("type") == "stream_subscribe_response":
            if msg.get("ok"):
                self.page_realtime.update_stream_state(msg)
            return
        if msg.get("type") == "stream_control_response":
            action = msg.get("action", "")
            if action == "start" and msg.get("ok"):
                self.page_realtime.update_stream_state(msg)
                fps = msg.get("fps", 0)
                self.controller.log_message.emit(
                    "INFO", "检测", f"产线模拟已启动: {fps} FPS")
            elif action == "stop":
                self.page_realtime.update_stream_state({})
                self.controller.log_message.emit("INFO", "检测", "产线模拟已停止")
            elif action == "set_fps":
                self.page_realtime.update_stream_state(msg)

    def _on_nano_image_detect(self):
        """「下位机图片」入口：弹出下位机图片选择器（与本地图片同交互）。
        单次/多次模式控制单选/多选；选图后主界面预览，点「开始检测」触发检测。"""
        if not self.controller.tcp.is_connected:
            self.controller.log_message.emit(
                "WARN", "检测", "下位机图片检测需要连接 Nano，请先「重新连接」")
            if QApplication.platformName() != "offscreen":
                QMessageBox.information(
                    self, "下位机图片",
                    "下位机（Nano）未连接。\n\n"
                    "请先点击「重新连接」连上下位机，\n"
                    "再选择下位机图片。")
            return
        if self.stream_engine.is_running:
            self._on_stop()
        from PyQt5.QtWidgets import QDialog
        from components.nano_image_picker import NanoImagePickerDialog
        multi = self.page_realtime.get_detect_mode() == "多次检测"
        dlg = NanoImagePickerDialog(
            tcp_client=self.controller.tcp, multi=multi, parent=self,
            default_dir=self.cfg.get("state", {}).get("last_nano_image_dir", ""))
        if dlg.exec_() != QDialog.Accepted or not dlg.selected_names:
            return
        names = list(dlg.selected_names)
        self._nano_image_dir = dlg.image_dir or self._nano_image_dir
        self._nano_image_names = names
        self._nano_image_active = True
        self._nano_image_idx = 0
        self._nano_file_pending = ""
        self._nano_wait_preview_detect = ""
        self._nano_start_pending = False
        self._multi_image_paths = [self._nano_path_of(n) for n in names]
        self._multi_image_idx = 0
        self._multi_results.clear()
        self._on_stop()
        self._save_state(last_nano_image_dir=self._nano_image_dir)
        self._nano_load_preview(self._multi_image_paths[0])
        self.page_realtime.update_nav(0, len(names))
        label = f"Nano 下位机（{'多图 ' + str(len(names)) + ' 张' if len(names) > 1 else '单图'}）"
        self.page_realtime.set_source(label, "#22d3ee")
        self.controller.log_message.emit(
            "INFO", "检测", f"已选择下位机图片 {len(names)} 张，点击「开始检测」检测")

    def _on_batch_result(self, data: dict):
        """批量检测结果回传：进入主流程（KPI / 历史 / 数据库）"""
        try:
            self.controller.ingest_result(
                data.get("dets", []), data.get("frame"), data.get("image_path", ""))
        except Exception as e:
            self._log_tcp_debug("WARN", f"批量检测结果入库失败: {e}")

    def closeEvent(self, event):
        try:
            self.stream_engine.stop()
            self.ng_saver.close()
        except Exception:
            pass
        self.controller.close()
        super().closeEvent(event)

    # ================= 边缘缩放 =================
    def _edge_dir(self, pos):
        r = self.rect()
        x, y = pos.x(), pos.y()
        m = _EDGE_MARGIN
        left, right = x <= m, x >= r.width() - m
        top, bottom = y <= m, y >= r.height() - m
        if top and left:
            return "top-left"
        if top and right:
            return "top-right"
        if bottom and left:
            return "bottom-left"
        if bottom and right:
            return "bottom-right"
        if left:
            return "left"
        if right:
            return "right"
        if top:
            return "top"
        if bottom:
            return "bottom"
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self._maximized:
            d = self._edge_dir(event.pos())
            if d:
                self._resize_dir = d
                self._resize_start = event.globalPos()
                self._resize_rect = QRect(self.geometry())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_dir:
            delta = event.globalPos() - self._resize_start
            r = QRect(self._resize_rect)
            d = self._resize_dir
            mw, mh = self.minimumWidth(), self.minimumHeight()
            if "right" in d:
                r.setRight(max(r.left() + mw, r.right() + delta.x()))
            if "bottom" in d:
                r.setBottom(max(r.top() + mh, r.bottom() + delta.y()))
            if "left" in d:
                r.setLeft(min(r.right() - mw, r.left() + delta.x()))
            if "top" in d:
                r.setTop(min(r.bottom() - mh, r.top() + delta.y()))
            self.setGeometry(r)
            event.accept()
            return
        if not self._maximized:
            d = self._edge_dir(event.pos())
            cur = Qt.ArrowCursor
            if d:
                cur = {"left": Qt.SizeHorCursor, "right": Qt.SizeHorCursor,
                       "top": Qt.SizeVerCursor, "bottom": Qt.SizeVerCursor,
                       "top-left": Qt.SizeFDiagCursor,
                       "bottom-right": Qt.SizeFDiagCursor,
                       "top-right": Qt.SizeBDiagCursor,
                       "bottom-left": Qt.SizeBDiagCursor}[d]
            self.setCursor(cur)

    def mouseReleaseEvent(self, event):
        if self._resize_dir:
            self._resize_dir = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _toggle_maximize(self):
        if self._maximized:
            self.showNormal()
            self._maximized = False
        else:
            self.showMaximized()
            self._maximized = True


def _deep_default() -> dict:
    import json
    return json.loads(json.dumps(cfg_mod.DEFAULT_CONFIG))


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    qss = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "assets", "qss", "dark_theme.qss")
    if os.path.exists(qss):
        with open(qss, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    win = MainWindow()
    win.showMaximized()
    sys.exit(app.exec_())


def _crash_log(tb: str):
    """把异常写入 data/crash.log（pythonw 启动无控制台，用于排错）"""
    try:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "crash.log"), "a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {tb}\n")
    except Exception:
        pass


def _install_excepthook():
    """全局未捕获异常 → 写 crash.log（Qt 槽内异常默认只打到 stderr，pythonw 下不可见）"""
    def hook(exc_type, exc_val, exc_tb):
        import traceback as _tb
        text = "".join(_tb.format_exception(exc_type, exc_val, exc_tb))
        try:
            sys.stderr.write(text)
        except Exception:
            pass
        _crash_log(text)
    sys.excepthook = hook


if __name__ == "__main__":
    _install_excepthook()
    try:
        main()
    except Exception:
        traceback.print_exc()
        tb = traceback.format_exc()
        _crash_log(tb)
        QMessageBox.critical(None, "启动失败", f"程序启动失败：\n{tb}")
