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
from core.frame_source import SimFrameSource
from core.stream_engine import StreamEngine
from core.model_manager_ctl import ModelManagerCtl
from core.ng_saver import NGSaver
from core import config as cfg_mod

_EDGE_MARGIN = 6


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
        self.model_ctl = ModelManagerCtl(self.controller.tcp)
        self.stream_engine = StreamEngine(infer_mode="sim")
        self.ng_saver = NGSaver(self.cfg.get("storage", {}).get(
            "save_path", "D:/Inspect/Images"))
        self._last_frame = None
        self._plc_running = False
        # 本地图片模式：加载图片后直接显示在预览区，点「开始检测」做单帧推理
        self._local_image = None          # numpy BGR 帧
        self._local_image_path = ""       # 图片路径
        self._local_image_active = False  # 是否正处于本地图片模式
        self._nano_active = False         # 是否检测到开发板通信（自动切换开发板模型）
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
        self.page_realtime.roi_changed.connect(self._on_roi_changed)
        self.page_realtime.load_model_requested.connect(self._on_load_model)
        self.page_realtime.model_mgr_requested.connect(self._on_model_mgr)
        self.page_realtime.save_path_changed.connect(self._on_save_path_changed)
        self.page_realtime.reconnect_requested.connect(self._on_tcp_reconnect)

        # 参数页
        self.page_param.params_apply_requested.connect(self._on_params_apply)
        self.page_param.save_config_requested.connect(self._on_save_config)
        self.page_param.reset_requested.connect(self._on_reset_config)
        self.page_param.roi_changed.connect(self._on_roi_changed)
        self.page_param.load_model_requested.connect(self._on_load_model)

        # 历史页
        self.page_history.query_requested.connect(self._on_history_query)
        self.page_history.export_requested.connect(self._on_history_export)

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
            msgs.append(("INFO", "系统", "等待连接设备或加载本地图片"))
        for lv, mod, msg in msgs:
            self.controller.log_message.emit(lv, mod, msg)

    def _auto_connect(self):
        """启动时自动连接下位机（开发板可能后开机，失败后会持续后台重连）"""
        t = self.cfg.get("tcp", {})
        self.controller.configure_tcp(
            t.get("host", "192.168.1.101"), t.get("port", 8888),
            heartbeat=t.get("heartbeat", 5), retries=t.get("retries", 3),
            timeout=t.get("timeout", 10))
        self.controller.connect_tcp()
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
        # 如果实时流已经在跑，这次点击视为「停止」请求，避免重复启动多条流
        if self.stream_engine.is_running:
            self._on_stop()
            return

        # 只要已加载本地图片，就优先做单帧推理（用户选图后的预期行为）
        if self._local_image is not None:
            self.stream_engine.stop()
            self._local_image_active = True
            self._detect_local_image()
            return

        # 标记为实时流模式，清除本地图片模式
        self._local_image_active = False

        # 开发板已连接且自动切换生效：走 TCP 推理
        tcp_on = self.controller.tcp.is_connected and self._nano_active
        if tcp_on:
            self.stream_engine.set_infer_callback(self._tcp_infer)
            self.stream_engine.infer_mode = "tcp"
            self.stream_engine.set_frame_source(SimFrameSource())
            self.stream_engine.start()
            self.page_realtime.set_running(True)
            self.controller.log_message.emit("INFO", "检测", "开始检测（下位机推理）")
        else:
            # 没有连接下位机：明确提示，不再用模拟演示
            self.controller.log_message.emit(
                "WARN", "检测", "未连接下位机，无法开始实时检测")
            if QApplication.platformName() != "offscreen":
                QMessageBox.information(
                    self, "开始检测",
                    "当前未连接下位机，无法开始实时检测。\n\n"
                    "请进行以下操作之一：\n"
                    "1. 连接下位机并等待状态栏「模型」变绿\n"
                    "2. 点击「本地图片」选择一张图片进行单张检测")

    def _tcp_infer(self, frame):
        ok, buf = cv2.imencode(".jpg", frame)
        if ok:
            self.controller.send_image(buf.tobytes())

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
        """对本地图片做单帧推理：必须有有效的 .onnx/.pt 本地模型，否则弹窗提示"""
        # 确保不跟实时流同时跑
        if self.stream_engine.is_running:
            self.stream_engine.stop()
        frame = self._local_image
        path = self._local_image_path
        t0 = time.perf_counter()
        basename = os.path.basename(path)

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

        # 真实本地推理
        try:
            if ext == ".onnx":
                from core.local_infer import load_session, infer_frame
                session = load_session(self._local_model)
                dets = infer_frame(session, frame, 0.25, 0.45)
            else:
                # 先排除 TorchScript 模型（文件名含 torchscript 或加载后类型不符）
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
                from ultralytics import YOLO
                model = YOLO(self._local_model)
                results = model.predict(frame, conf=0.25, iou=0.45,
                                        verbose=False, imgsz=640, device="cpu")
                r = results[0]
                dets = []
                if r.boxes is not None and len(r.boxes) > 0:
                    boxes = r.boxes.xyxy.cpu().numpy()
                    confs = r.boxes.conf.cpu().numpy()
                    cls_ids = r.boxes.cls.cpu().numpy().astype(int)
                    from core.local_infer import NEU_CLASSES
                    for box, c, ci in zip(boxes, confs, cls_ids):
                        cls = NEU_CLASSES[ci] if ci < len(NEU_CLASSES) else f"cls{ci}"
                        dets.append((cls, float(c), *[float(v) for v in box]))
        except Exception as e:
            self.controller.log_message.emit(
                "ERROR", "检测", f"本地推理失败: {e}")
            if QApplication.platformName() != "offscreen":
                QMessageBox.warning(
                    self, "本地图片检测",
                    f"模型推理时出错:\n{e}\n\n"
                    "请检查模型文件是否完整，或尝试其他 .onnx / .pt 模型。")
            return

        ms = (time.perf_counter() - t0) * 1000
        # 交给 controller 统一入管线（KPI/写库/历史/预览画框）
        self.controller.ingest_result(dets, frame, path)
        self.controller.log_message.emit(
            "INFO", "检测",
            f"本地图片检测完成: {basename} "
            f"({'NG 缺陷×' + str(len(dets)) if dets else 'OK'})  耗时 {ms:.0f} ms")
        self.status_left.setText(
            f"本地图片: {basename}　| 检测完成 {ms:.0f} ms")

    def _on_stop(self):
        """停止实时流；保留本地图片模式，方便用户再次点击开始检测同一图片"""
        if self.stream_engine.is_running:
            self.stream_engine.stop()
        self.page_realtime.set_running(False)
        self.controller.log_message.emit("INFO", "检测", "检测已停止")
        if self._local_image_active and self._local_image is not None:
            self.status_left.setText(
                f"本地图片: {os.path.basename(self._local_image_path)}　|　已停止，可再次检测")

    def _on_stream_frame(self, frame):
        self._last_frame = frame

    def _on_fps(self, fps):
        self.status_left.setText(f"运行中　|　检测帧率: {fps:.0f} FPS")
        self.page_log.set_fps(fps)

    def _on_detection_result(self, result: dict):
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

    def _on_ng_alarm(self, count):
        self.controller.log_message.emit(
            "ERROR", "检测", f"连续 {count} 次检出缺陷，请检查产线状态！")
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

    def _on_reset_config(self):
        self.cfg = _deep_default()
        self.page_param.apply_config(self.cfg)
        self._sync_cfg_to_realtime()
        self.page_realtime.set_rois(self.cfg["rois"])
        self.page_param.set_rois(self.cfg["rois"])
        self.controller.log_message.emit("INFO", "系统", "已恢复默认配置")

    def _on_save_path_changed(self, path: str):
        self.cfg.setdefault("storage", {})["save_path"] = path
        cfg_mod.save_config(self.cfg)
        self.ng_saver.set_save_root(path)
        self.page_param.apply_config(self.cfg)
        self.controller.log_message.emit("INFO", "系统", f"保存路径已更新: {path}")

    def _on_params_apply(self, conf, iou):
        rois = [{"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]}
                for r in self._rois if r.get("enabled", True)]
        if self.controller.tcp.is_connected:
            self.controller.tcp.send_control(conf_thres=conf, iou_thres=iou, rois=rois)
            self.controller.log_message.emit(
                "INFO", "参数", f"已下发参数: conf={conf}, roi×{len(rois)}")
        else:
            self.controller.log_message.emit("INFO", "参数",
                                             f"参数已应用（本地）: conf={conf}")

    # ================= 模型 =================
    def _on_load_model(self, name):
        import os
        if name and os.path.isfile(name) and \
                name.lower().endswith((".onnx", ".pt")):
            # 本地模型：直接走 PC 本地推理（无需 Nano 在线）
            self._local_model = name
            self.controller.log_message.emit(
                "INFO", "模型",
                f"已选择本地模型 {os.path.basename(name)}，开始检测时启用本地推理")
            self.title_bar.set_model_tag(os.path.basename(name))
            return
        self._local_model = ""
        if self.controller.tcp.is_connected:
            self.controller.tcp.load_model(name)
            self.controller.log_message.emit("INFO", "模型", f"请求加载模型: {name}")
        else:
            self.controller.log_message.emit("WARN", "模型", "未连接下位机，模型未切换")
        self.title_bar.set_model_tag(name)

    def _on_model_mgr(self):
        from components.model_manager_dialog import ModelManagerDialog
        dlg = ModelManagerDialog(
            self.controller.tcp, self,
            default_model=self._local_model,
            default_model_dir=self.cfg.get("state", {}).get("last_model_dir", "")
        )
        dlg.local_model_selected.connect(self._on_local_model_selected)
        dlg.reconnect_requested.connect(self._on_model_mgr_reconnect)
        dlg.exec_()

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
        # 记忆 Nano 当前模型
        self._save_state(last_nano_model=active)

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
        # 记忆 Nano 当前模型
        self._save_state(last_nano_model=model)

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
                self.controller.log_message.emit(
                    "INFO", "PLC", f"测试通信成功 RTT={getattr(plc, '_last_rtt', 0):.1f}ms")
                self.page_comm.append_txrx(
                    f"[TX] 01 03 00 00 00 01　→　[RX] OK running={st['running']}")
            except Exception as e:
                self.controller.log_message.emit("ERROR", "PLC", f"测试通信失败: {e}")
        else:
            self.controller.log_message.emit("WARN", "PLC", "PLC 未连接，无法测试")

    def _on_tcp_reconnect(self):
        """手动重新连接下位机推理服务（断开旧线程后立即重连）"""
        self.controller.tcp.disconnect()
        t = self.cfg.get("tcp", {})
        self.controller.configure_tcp(
            t.get("host", "192.168.1.101"), t.get("port", 8888),
            heartbeat=t.get("heartbeat", 5), retries=t.get("retries", 3),
            timeout=t.get("timeout", 10))
        self.controller.connect_tcp()
        self.controller.log_message.emit(
            "INFO", "通信", f"正在重新连接下位机 {t.get('host', '192.168.1.101')}:{t.get('port', 8888)}...")

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
        else:
            self.controller.log_message.emit("WARN", "系统", "无可用帧，保存失败")

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
        act_model = menu.addAction("模型管理")
        menu.addSeparator()
        act_about = menu.addAction("关于")
        act = menu.exec_(self.title_bar.mapToGlobal(self._menu_pos()))
        if act == act_model:
            self._on_model_mgr()
        elif act == act_about:
            QMessageBox.about(self, "关于",
                              "工业缺陷检测上位机 v1.1\n"
                              "下位机: RK3568 / Jetson 系列\n"
                              "通信: TCP (4字节长度头 + JSON) / Modbus TCP / 串口\n"
                              "本地检测: ONNX / PT 模型 或 模拟演示")

    def _menu_pos(self):
        """菜单弹出位置：标题栏菜单按钮附近"""
        return self.title_bar.rect().topRight() - self.title_bar.rect().topLeft() \
            + self.title_bar.pos()

    def _on_local_image(self):
        """本地图片：选图后直接显示在实时预览区，不弹窗。
        点「开始检测」时对这张图做单帧推理。
        若取消选择且当前处于本地图片模式，则切回实时流模式。"""
        last_dir = self.cfg.get("state", {}).get("last_image_dir", "")
        path, _ = QFileDialog.getOpenFileName(
            self, "选择检测图片", last_dir,
            "图片文件 (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            # 取消选择：如果当前是本地图片模式，则清除并切回实时流模式
            if self._local_image_active:
                self._local_image = None
                self._local_image_path = ""
                self._local_image_active = False
                self.status_left.setText("就绪　|　检测帧率: -- FPS")
                self.controller.log_message.emit(
                    "INFO", "检测", "已清除本地图片，切换为实时流模式")
            return
        # 记忆目录
        self._save_state(last_image_dir=os.path.dirname(path) or "")
        # cv2.imread 不支持中文路径，用 np.fromfile + imdecode 替代
        import numpy as np
        try:
            _raw = np.fromfile(path, dtype=np.uint8)
            _img = cv2.imdecode(_raw, cv2.IMREAD_COLOR)
        except Exception:
            _img = None
        if _img is None:
            self.controller.log_message.emit("ERROR", "检测", f"无法读取图片: {path}")
            if QApplication.platformName() != "offscreen":
                QMessageBox.warning(self, "本地图片", f"无法读取图片:\n{path}")
            return

        # 停止实时流（如果在运行），本地图片只做单帧
        self._on_stop()

        # 设置本地图片模式
        self._local_image = _img
        self._local_image_path = path
        self._local_image_active = True

        # 直接显示在预览区
        rgb = cv2.cvtColor(_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        self.page_realtime.update_image(
            QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy())
        self.page_realtime.update_detections([])  # 清除之前的检测框

        basename = os.path.basename(path)
        self.controller.log_message.emit(
            "INFO", "检测",
            f"已加载本地图片: {basename}（点击「开始检测」进行推理）")
        self.status_left.setText(f"本地图片: {basename}　|　点击开始检测")

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
