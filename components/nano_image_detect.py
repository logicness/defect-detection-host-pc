# -*- coding: utf-8 -*-
"""
下位机图片检测对话框（2026-08-20 新增）
=================================================
- 查看下位机（Nano）固定图片文件夹中的图片：缩略图网格 + 按需拉取大图预览
- 检测默认使用下位机当前激活模型（无需在上位机选择模型）
- 支持批量检测 + 轮数（多次检测），由 Nano 端会话驱动并推送进度
- 结果回传：detections + 标注缩略图，逐张展示；可保存标注图
- 结果经 result_committed 信号交给 main 统一入管线（KPI/写库/历史）
"""
import base64
import os
import time
from datetime import datetime

import cv2
import numpy as np

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox,
    QWidget, QApplication, QListWidget, QListWidgetItem, QAbstractItemView,
    QProgressBar, QCheckBox, QFileDialog
)
from PyQt5.QtCore import pyqtSignal, Qt, QSize
from PyQt5.QtGui import QPixmap, QIcon, QImage

from components.image_preview import ImagePreview
from components.common_widgets import Card, StatusLight, SpinBox

_SAVE_DIR = "D:/Inspect/LocalDetect"
_THUMB_ICON = QSize(120, 90)


class NanoImageDetectDialog(QDialog):
    """下位机图片检测对话框：Nano 本地图片 + Nano 模型，检测结果回传上位机"""

    result_committed = pyqtSignal(dict)   # {"dets": ui列表, "image_path": str, "frame": bgr}

    def __init__(self, tcp_client=None, nano_model_name="", parent=None,
                 default_image_dir=""):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self._tcp = tcp_client
        self._nano_model = nano_model_name or ""
        self._default_image_dir = default_image_dir
        self.image_dir = ""          # 当前下位机图片目录（Nano 返回）
        self._images = []            # [{name, thumb_b64, w, h, mtime}]
        self._current_name = ""      # 当前预览图片文件名
        self._frame = None           # 当前预览 BGR
        self._dets = []              # UI 格式 [(cls, conf, x1,y1,x2,y2)]
        self._last_ms = 0.0
        self._batch_running = False
        self._batch_session = ""
        self._result_count = 0       # 已收到结果条目数
        self._total_count = 0        # 本次任务总量
        self._ng_count = 0
        self._ok_count = 0
        self._fail_names = []
        self._connected_once = False
        self._wired = False

        self.setWindowTitle("下位机图片检测")
        self.setMinimumSize(900, 720)
        self.resize(1120, 800)
        self._build_ui()
        self._wire_tcp()
        self._refresh()

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 自定义标题栏（无边框窗口手动拖动）
        title_bar = QWidget()
        title_bar.setFixedHeight(40)
        title_bar.setStyleSheet("background-color:#10151f; border-bottom:1px solid #1f2937;")
        tlay = QHBoxLayout(title_bar)
        tlay.setContentsMargins(16, 0, 12, 0)
        tlay.setSpacing(8)
        t_title = QLabel("下位机图片检测")
        t_title.setStyleSheet("color:#f1f5f9; font-size:16px; font-weight:600; background:transparent;")
        tlay.addWidget(t_title)
        tlay.addStretch()
        t_close = QPushButton("✕")
        t_close.setFixedSize(36, 28)
        t_close.setStyleSheet(
            "QPushButton{border:none; background:transparent; color:#94a3b8; font-size:14px;}"
            "QPushButton:hover{background:#dc2626; color:#ffffff; border-radius:4px;}")
        t_close.clicked.connect(self.reject)
        tlay.addWidget(t_close)
        self._drag_pos = None
        title_bar.mousePressEvent = self._title_mouse_press
        title_bar.mouseMoveEvent = self._title_mouse_move
        title_bar.mouseReleaseEvent = lambda e: setattr(self, '_drag_pos', None)
        root.addWidget(title_bar)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.setSpacing(10)

        # 目录行
        drow = QHBoxLayout()
        self.lbl_dir = QLabel("下位机图片目录: --")
        self.lbl_dir.setStyleSheet("color:#e2e8f0; font-size:14px; background:transparent;")
        self.lbl_dir.setWordWrap(True)
        drow.addWidget(self.lbl_dir, 1)
        self.btn_refresh = QPushButton("↺ 刷新列表")
        self.btn_refresh.setFixedHeight(34)
        self.btn_refresh.clicked.connect(self._refresh)
        self.btn_set_dir = QPushButton("设置文件夹...")
        self.btn_set_dir.setFixedHeight(34)
        self.btn_set_dir.setToolTip("把下位机图片文件夹切换到其他目录（需在 Nano 上可访问）")
        self.btn_set_dir.clicked.connect(self._on_set_dir)
        drow.addWidget(self.btn_refresh)
        drow.addWidget(self.btn_set_dir)
        bl.addLayout(drow)

        # 中部：缩略图网格 + 预览
        mid = QHBoxLayout()
        mid.setSpacing(10)
        card_list = Card("下位机图片（勾选后点「开始检测」）")
        self.list_images = QListWidget()
        self.list_images.setViewMode(QListWidget.IconMode)
        self.list_images.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_images.setIconSize(_THUMB_ICON)
        self.list_images.setResizeMode(QListWidget.Adjust)
        self.list_images.setMovement(QListWidget.Static)
        self.list_images.setSpacing(6)
        self.list_images.itemSelectionChanged.connect(self._on_selection)
        self.list_images.setMinimumWidth(420)
        card_list.body.addWidget(self.list_images, 1)
        mid.addWidget(card_list, 3)

        card_preview = Card("预览（结果画框）")
        self.preview = ImagePreview()
        self.preview.setMinimumHeight(420)
        card_preview.body.addWidget(self.preview, 1)
        mid.addWidget(card_preview, 4)
        bl.addLayout(mid, 1)

        # 参数行
        prow = QHBoxLayout()
        prow.addWidget(QLabel("检测轮数:"))
        self.spin_rounds = SpinBox()
        self.spin_rounds.setRange(1, 10)
        self.spin_rounds.setValue(1)
        self.spin_rounds.setToolTip("多次检测：对所选图片重复检测 N 轮")
        prow.addWidget(self.spin_rounds)
        self.chk_annotate = QCheckBox("回传标注图")
        self.chk_annotate.setChecked(True)
        self.chk_annotate.setStyleSheet("color:#cbd5e1; background:transparent;")
        prow.addWidget(self.chk_annotate)
        prow.addSpacing(10)
        self.light_model = StatusLight("Nano 下位机模型")
        prow.addWidget(self.light_model)
        prow.addStretch()
        bl.addLayout(prow)

        # 操作行
        orow = QHBoxLayout()
        self.btn_start = QPushButton("▶  开始检测")
        self.btn_start.setObjectName("btnPrimary")
        self.btn_start.setFixedHeight(40)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop = QPushButton("■  停止")
        self.btn_stop.setObjectName("btnDanger")
        self.btn_stop.setFixedHeight(40)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_save = QPushButton("保存标注图")
        self.btn_save.setFixedHeight(40)
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._on_save)
        orow.addWidget(self.btn_start)
        orow.addWidget(self.btn_stop)
        orow.addWidget(self.btn_save)
        orow.addStretch()
        self.lbl_result = QLabel("结果: --")
        self.lbl_result.setStyleSheet("color:#e2e8f0; font-size:15px; background:transparent;")
        orow.addWidget(self.lbl_result)
        bl.addLayout(orow)

        # 进度行
        prog = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("0/0")
        self.progress.setFixedHeight(22)
        prog.addWidget(self.progress, 1)
        bl.addLayout(prog)

        # 结果信息
        info = Card("检测结果")
        row = QHBoxLayout()
        self.lbl_defect = QLabel("缺陷: --")
        self.lbl_conf = QLabel("置信度: --")
        self.lbl_time = QLabel("耗时: --")
        for l in (self.lbl_defect, self.lbl_conf, self.lbl_time):
            l.setStyleSheet("color:#e2e8f0; font-size:15px; background:transparent;")
            row.addWidget(l)
            row.addStretch()
        info.body.addLayout(row)
        bl.addWidget(info)

        # 关闭
        btns = QHBoxLayout()
        btns.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.setFixedWidth(120)
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_close)
        bl.addLayout(btns)

        root.addWidget(body)

        # 初始状态
        if self._tcp is None or not getattr(self._tcp, "is_connected", False):
            self._set_busy(False)
            self.btn_start.setEnabled(False)
            self.btn_refresh.setEnabled(False)
            self.btn_set_dir.setEnabled(False)
            self.lbl_dir.setText("下位机图片目录: --（Nano 未连接）")
            self.light_model.set_status(2, "Nano 未连接")

    # ---------------- 标题栏拖动 ----------------
    def _title_mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()

    def _title_mouse_move(self, event):
        if hasattr(self, '_drag_pos') and self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)

    # ---------------- TCP 接线 ----------------
    def _wire_tcp(self):
        if self._wired or self._tcp is None:
            return
        t = self._tcp
        t.nano_image_dir_received.connect(self._on_nano_dir)
        t.nano_images_received.connect(self._on_nano_images)
        t.nano_image_received.connect(self._on_nano_image)
        t.nano_batch_received.connect(self._on_nano_batch)
        t.nano_batch_progress_received.connect(self._on_nano_batch_progress)
        t.nano_batch_item_received.connect(self._on_nano_batch_item)
        t.nano_batch_done_received.connect(self._on_nano_batch_done)
        t.disconnected.connect(self._on_nano_disconnected)
        self._wired = True

    def _unwire_tcp(self):
        if not self._wired or self._tcp is None:
            return
        t = self._tcp
        for sig in (t.nano_image_dir_received, t.nano_images_received,
                    t.nano_image_received, t.nano_batch_received,
                    t.nano_batch_progress_received, t.nano_batch_item_received,
                    t.nano_batch_done_received, t.disconnected):
            try:
                sig.disconnect(self._on_nano_dir)
                sig.disconnect(self._on_nano_images)
                sig.disconnect(self._on_nano_image)
                sig.disconnect(self._on_nano_batch)
                sig.disconnect(self._on_nano_batch_progress)
                sig.disconnect(self._on_nano_batch_item)
                sig.disconnect(self._on_nano_batch_done)
                sig.disconnect(self._on_nano_disconnected)
            except Exception:
                pass
        self._wired = False

    # ---------------- 列表刷新 ----------------
    def _refresh(self):
        if self._tcp is None or not getattr(self._tcp, "is_connected", False):
            return
        self.lbl_dir.setText("下位机图片目录: 查询中...")
        self._tcp.request_nano_image_dir("get")
        self._tcp.request_nano_images(offset=0, limit=500, thumb=True, thumb_size=256)

    def _on_nano_dir(self, msg):
        if msg.get("ok"):
            self.image_dir = msg.get("dir", "")
            self.lbl_dir.setText(f"下位机图片目录: {self.image_dir}")
            # 记忆目录（父窗口是 MainWindow 时）
            if hasattr(self.parent(), '_save_state') and callable(self.parent()._save_state):
                try:
                    self.parent()._save_state(last_nano_image_dir=self.image_dir)
                except Exception:
                    pass

    def _on_nano_images(self, msg):
        if not msg.get("ok"):
            self.lbl_dir.setText(f"图片列表获取失败: {msg.get('error', '未知错误')}")
            return
        self._images = [i for i in msg.get("images", [])]
        self.list_images.clear()
        for item in self._images:
            it = QListWidgetItem()
            thumb = item.get("thumb_b64", "")
            if thumb:
                pm = QPixmap()
                pm.loadFromData(base64.b64decode(thumb))
                it.setIcon(QIcon(pm))
            it.setText(item.get("name", ""))
            it.setData(Qt.UserRole, item.get("name", ""))
            it.setSizeHint(QSize(150, 116))
            it.setToolTip(item.get("name", ""))
            self.list_images.addItem(it)
        total = msg.get("total", len(self._images))
        self.lbl_dir.setText(f"下位机图片目录: {self.image_dir}（共 {total} 张，显示 {len(self._images)}）")
        self.light_model.set_status(1, f"Nano 下位机: {self._nano_model or '当前模型'}")

    def _on_selection(self):
        if self._batch_running:
            return
        items = self.list_images.selectedItems()
        if not items:
            return
        name = items[-1].data(Qt.UserRole)
        if name and name != self._current_name:
            self._current_name = name
            self._dets = []
            self.preview.clear_detections()
            self.btn_save.setEnabled(False)
            self._tcp.request_nano_image(name, 640)

    def _on_nano_image(self, msg):
        if not msg.get("ok"):
            self.lbl_result.setText(f"结果: 预览获取失败 {msg.get('error', '')}")
            return
        raw = base64.b64decode(msg["image_b64"])
        img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            self.lbl_result.setText("结果: 预览图解码失败")
            return
        self._frame = img
        self._show_frame()
        self.lbl_result.setText(f"结果: 已加载 {msg.get('name', '')}")

    # ---------------- 检测 ----------------
    def _on_start(self):
        if self._tcp is None or not getattr(self._tcp, "is_connected", False):
            if QApplication.platformName() != "offscreen":
                QMessageBox.warning(self, "下位机图片检测",
                                    "Nano 下位机未连接，无法检测。\n请先连接下位机。")
            return
        if self._batch_running:
            return
        items = self.list_images.selectedItems()
        names = [i.data(Qt.UserRole) for i in items] if items else []
        if not names:
            names = [i.get("name", "") for i in self._images]
        names = [n for n in names if n]
        if not names:
            if QApplication.platformName() != "offscreen":
                QMessageBox.information(self, "下位机图片检测",
                                        "下位机图片目录为空，请先在下位机放入图片。")
            return
        rounds = self.spin_rounds.value()
        self._batch_running = True
        self._result_count = 0
        self._ng_count = 0
        self._ok_count = 0
        self._fail_names = []
        self._results = {}
        self._set_busy(True)
        self.progress.setValue(0)
        self.lbl_result.setText(f"结果: 下位机批量检测中（{len(names)} 张 × {rounds} 轮）...")
        self._tcp.start_nano_batch(names, rounds=rounds,
                                   annotate=self.chk_annotate.isChecked())

    def _on_nano_batch(self, msg):
        if msg.get("ok"):
            self._batch_session = msg.get("session_id", "")
            self._total_count = msg.get("total", 0)
            self.progress.setRange(0, max(1, self._total_count))
        else:
            self._batch_running = False
            self._set_busy(False)
            self.lbl_result.setText(f"结果: 启动失败 {msg.get('error', '')}")
            if QApplication.platformName() != "offscreen":
                QMessageBox.warning(self, "下位机图片检测", msg.get("error", "批量启动失败"))

    def _on_nano_batch_item(self, msg):
        if not self._batch_running:
            return
        self._result_count += 1
        name = msg.get("name", "")
        if not msg.get("ok"):
            self._fail_names.append(name)
            self.lbl_result.setText(f"结果: [{self._result_count}/{self._total_count}] {name} 失败: {msg.get('error', '')}")
            return
        ok = msg.get("det_count", 0) > 0
        if ok:
            self._ng_count += 1
        else:
            self._ok_count += 1
        # 标注图 → 预览
        if msg.get("annot_b64"):
            raw = base64.b64decode(msg["annot_b64"])
            frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                self._frame = frame
                self._show_frame()
        # 原始 detections → UI 格式（按下位机模型映射类别名）
        from core.class_names import resolve_class_names, class_name_of
        class_names = resolve_class_names(self._nano_model)
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
        self._dets = dets
        self._last_ms = msg.get("timing_ms", 0) or 0
        self._results[name] = {"dets": list(dets), "ms": self._last_ms, "ok": ok}
        self.preview.set_detections(dets)
        self.btn_save.setEnabled(True)
        path = f"nano://{self.image_dir}/{name}"
        self.result_committed.emit({"dets": list(dets), "image_path": path,
                                    "frame": self._frame})
        if ok:
            top = max(dets, key=lambda d: d[1])
            self.lbl_defect.setText(f"缺陷: {top[0]} × {len(dets)}")
            self.lbl_conf.setText(f"置信度: {top[1]:.2f}")
        else:
            self.lbl_defect.setText("缺陷: 无（OK）")
            self.lbl_conf.setText("置信度: --")
        self.lbl_time.setText(f"耗时: {self._last_ms:.0f} ms")
        self.lbl_result.setText(f"结果: [{self._result_count}/{self._total_count}] {name} {'NG' if ok else 'OK'}")

    def _on_nano_batch_progress(self, msg):
        if not self._batch_running:
            return
        done = msg.get("done", self._result_count)
        self.progress.setValue(done)
        self.progress.setFormat(f"{done}/{msg.get('total', self._total_count)}")

    def _on_nano_batch_done(self, msg):
        self._batch_running = False
        self._set_busy(False)
        self.progress.setValue(msg.get("total", self.progress.maximum()))
        self.progress.setFormat(f"{msg.get('done', msg.get('total', 0))}/{msg.get('total', 0)}")
        stopped = msg.get("stopped", False)
        failed = msg.get("failed", []) or []
        tag = "已停止" if stopped else "完成"
        self.lbl_result.setText(
            f"结果: 批量检测{tag} OK={msg.get('ok_count', 0)} NG={msg.get('ng_count', 0)}"
            f" 失败={len(failed)} 平均{msg.get('avg_ms', 0):.0f}ms")
        if failed and QApplication.platformName() != "offscreen":
            QMessageBox.warning(
                self, "下位机图片检测",
                f"批量检测{tag}。\n失败 {len(failed)} 张: {', '.join(failed[:8])}"
                + ("..." if len(failed) > 8 else ""))

    def _on_stop(self):
        if self._batch_running:
            self._tcp.stop_nano_batch(self._batch_session or None)

    def _on_nano_disconnected(self):
        """Nano 断连：中止当前批量并恢复 UI（不弹窗，避免断连刷屏）"""
        if self._batch_running:
            self._batch_running = False
            self._set_busy(False)
            self.lbl_result.setText("结果: Nano 已断开，批量中止")
            self.light_model.set_status(2, "Nano 未连接")

    # ---------------- 辅助 ----------------
    def _set_busy(self, busy: bool):
        self.btn_start.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        self.btn_refresh.setEnabled(not busy)
        self.btn_set_dir.setEnabled(not busy)
        self.list_images.setEnabled(not busy)
        self.spin_rounds.setEnabled(not busy)

    def _show_frame(self):
        if self._frame is None:
            return
        rgb = cv2.cvtColor(self._frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        self.preview.set_image(QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy())

    def _on_set_dir(self):
        """设置下位机图片文件夹：输入路径（Nano 侧路径），发送 set 指令"""
        from PyQt5.QtWidgets import QInputDialog
        if self._tcp is None or not self._tcp.is_connected:
            return
        cur = self.image_dir or "/home/nvidia/defect_detection/images/input"
        path, ok = QInputDialog.getText(
            self, "设置下位机图片文件夹",
            "输入 Nano 上的图片目录路径（需真实存在）:", text=cur)
        if ok and path.strip():
            self._tcp.request_nano_image_dir("set", path.strip())

    # ---------------- 保存标注图 ----------------
    def _on_save(self):
        if self._frame is None:
            return
        os.makedirs(_SAVE_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(_SAVE_DIR, f"nano_{stamp}.jpg")
        img = self._frame.copy()
        for cls, conf, x1, y1, x2, y2 in self._dets:
            cv2.rectangle(img, (int(x1), int(y1)), (int(x2), int(y2)),
                          (0, 0, 255), 2)
            cv2.putText(img, f"{cls} {conf:.2f}", (int(x1), max(18, int(y1) - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        cv2.imwrite(out_path, img)
        try:
            csv_path = os.path.join(_SAVE_DIR, "records.csv")
            new = not os.path.exists(csv_path)
            with open(csv_path, "a", encoding="utf-8-sig") as f:
                if new:
                    f.write("时间,图片,模型,结果,缺陷数,耗时ms\n")
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')},"
                        f"nano://{self.image_dir}/{self._current_name},"
                        f"{self._nano_model or 'Nano 下位机'},"
                        f"{'NG' if self._dets else 'OK'},"
                        f"{len(self._dets)},{self._last_ms:.0f}\n")
        except Exception:
            pass
        if QApplication.platformName() != "offscreen":
            QMessageBox.information(self, "下位机图片检测", f"标注图已保存:\n{out_path}")

    # ---------------- 关闭 ----------------
    def reject(self):
        if self._batch_running:
            self._on_stop()
        self._unwire_tcp()
        super().reject()

    def accept(self):
        if self._batch_running:
            self._on_stop()
        self._unwire_tcp()
        super().accept()

    def closeEvent(self, event):
        if self._batch_running:
            self._on_stop()
        self._unwire_tcp()
        super().closeEvent(event)
