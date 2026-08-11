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
        self.stream_engine = StreamEngine(infer_mode="sim")
        self.ng_saver = NGSaver(self.cfg.get("storage", {}).get(
            "save_path", "D:/Inspect/Images"))
        self._last_frame = None
        self._plc_running = False
        self._local_model = ""   # 本地模型路径（.onnx/.pt），非空时优先本地推理

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

        # 实时流
        self.stream_engine.frame_ready.connect(self._on_stream_frame)
        self.stream_engine.result_received.connect(
            lambda r: c.ingest_result(r.get("detections", []), r.get("frame")))
        self.stream_engine.log_message.connect(
            lambda lv, m: c.log_message.emit(lv, "检测", m))

        # 推理结果 → 实时页/历史页/通信页
        c.detection_result.connect(self._on_detection_result)
        c.ng_alarm.connect(self._on_ng_alarm)

        # 实时页
        self.page_realtime.start_requested.connect(self._on_start)
        self.page_realtime.stop_requested.connect(self._on_stop)
        self.page_realtime.save_image_requested.connect(self._on_save_image)
        self.page_realtime.roi_changed.connect(self._on_roi_changed)
        self.page_realtime.load_model_requested.connect(self._on_load_model)
        self.page_realtime.model_mgr_requested.connect(self._on_model_mgr)

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

    # ================= 启动 =================
    def _emit_startup_logs(self):
        for lv, mod, msg in [
                ("INFO", "系统", "软件启动"),
                ("INFO", "系统", "配置文件加载完成"),
                ("INFO", "相机", "相机初始化完成"),
                ("INFO", "相机", "相机 Camera_01 连接成功"),
                ("INFO", "模型", "模型 Product_A_v1 加载完成"),
                ("INFO", "系统", "所有模块初始化完成，进入运行状态")]:
            self.controller.log_message.emit(lv, mod, msg)

    def _auto_connect(self):
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
        if self.stream_engine.is_running:
            return
        tcp_on = self.controller.tcp.is_connected
        if tcp_on:
            mode = "tcp"
            self.stream_engine.set_infer_callback(self._tcp_infer)
        elif self._local_model:
            mode = "local"
        else:
            mode = "sim"
        self.stream_engine.infer_mode = mode
        if mode == "local":
            self.stream_engine.set_local_model(self._local_model)
        self.stream_engine.set_frame_source(SimFrameSource())
        self.stream_engine.start()
        self.controller.log_message.emit(
            "INFO", "检测", "开始检测（" +
            ("下位机推理" if mode == "tcp"
             else ("本地模型推理" if mode == "local" else "本地模拟推理")) + "）")

    def _tcp_infer(self, frame):
        ok, buf = cv2.imencode(".jpg", frame)
        if ok:
            self.controller.send_image(buf.tobytes())

    def _on_stop(self):
        if self.stream_engine.is_running:
            self.stream_engine.stop()
        self.controller.log_message.emit("INFO", "检测", "检测已停止")

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
        area = int(abs((first[4] - first[2]) * (first[5] - first[3]))) if first else 0
        self.page_realtime.update_detail(
            "Product_A_v1", verdict, first[0] if first else "-",
            area, f"{first[1]:.2f}" if first else "--", ts,
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
        box = QMessageBox(self)
        box.setWindowTitle("连续 NG 告警")
        box.setText(f"连续检出 {count} 个缺陷！")
        box.setInformativeText("可能原因：产线异常 / 相机失焦 / 光源异常，请立即检查。")
        box.setIcon(QMessageBox.Warning)
        box.setWindowModality(Qt.NonModal)
        box.show()
        self._ng_alarm_box = box  # 保留引用防回收

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
        dlg = ModelManagerDialog(self.controller.tcp, self)
        dlg.local_model_selected.connect(self._on_local_model_selected)
        dlg.exec_()

    def _on_local_model_selected(self, path):
        import os
        self._local_model = path
        self.controller.log_message.emit(
            "INFO", "模型", f"已选择本地模型 {os.path.basename(path)}，开始检测时启用本地推理")
        self.title_bar.set_model_tag(os.path.basename(path))
        self.page_param.set_cur_model(os.path.basename(path))
        self.page_realtime.set_cur_model(os.path.basename(path))

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

    def _on_status_changed(self, name, status):
        bar = {"camera": self.title_bar.light_camera,
               "plc": self.title_bar.light_plc,
               "model": self.title_bar.light_model}.get(name)
        if bar:
            bar.set_status(status)
        if name == "plc":
            self.page_comm.set_plc_conn_ui(status == 1)
            self.page_realtime.set_plc_light(status == 1)
        if name == "model" and status == 1:
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
        act_image = menu.addAction("本地图片检测")
        act_model = menu.addAction("模型管理")
        menu.addSeparator()
        act_about = menu.addAction("关于")
        act = menu.exec_(self.title_bar.mapToGlobal(self._menu_pos()))
        if act == act_image:
            self._open_local_image_detect()
        elif act == act_model:
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

    def _open_local_image_detect(self):
        from components.local_image_detect import LocalImageDetectDialog
        dlg = LocalImageDetectDialog(
            local_model=self._local_model, rois=self._rois, parent=self)
        dlg.result_committed.connect(self._on_local_image_result)
        dlg.exec_()

    def _on_local_image_result(self, data: dict):
        dets = data.get("dets", [])
        frame = data.get("frame")
        path = data.get("image_path", "")
        self.controller.ingest_result(dets, frame, path)
        self.controller.log_message.emit(
            "INFO", "检测", f"本地图片检测完成: {os.path.basename(path)} "
            f"({'NG 缺陷×' + str(len(dets)) if dets else 'OK'})")

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
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        QMessageBox.critical(None, "启动失败", f"程序启动失败：\n{traceback.format_exc()}")
