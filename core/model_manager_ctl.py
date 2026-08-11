# -*- coding: utf-8 -*-
"""
上位机模型管理控制器（对齐 Nano 版逻辑）
========================================
统一监听 TCPClient 的模型管理信号，维护模型清单/激活状态/上传编译状态，
供参数设置页、实时检测页、模型管理对话框共用（避免重复接线）。

动作：refresh（拉清单）/ load（切换）/ upload（分片上传）/ delete（删除）
状态：models / active / uploading / compiling
"""
from PyQt5.QtCore import QObject, pyqtSignal


class ModelManagerCtl(QObject):
    """模型管理控制器：动作入口 + 状态中枢"""

    # 对外信号（页面/对话框订阅）
    models_updated = pyqtSignal(dict)     # {"models": [...], "active": "..."} 清单变化
    load_result = pyqtSignal(dict)        # model_load_response
    upload_state = pyqtSignal(dict)       # model_upload_response
    compile_progress = pyqtSignal(dict)   # model_status（编译进度）
    delete_result = pyqtSignal(dict)      # model_delete_response
    error_message = pyqtSignal(str)       # 通用错误提示

    def __init__(self, tcp_client):
        super().__init__()
        self._tcp = tcp_client
        self.models = []      # 最近一次模型清单
        self.active = ""      # 当前激活模型 name
        self.uploading = False
        self.compiling = False

        # 订阅 TCP 底层信号
        tcp_client.model_list_received.connect(self._on_list)
        tcp_client.model_load_received.connect(self._on_load)
        tcp_client.model_upload_received.connect(self._on_upload)
        tcp_client.model_status_received.connect(self._on_status)
        tcp_client.model_delete_received.connect(self._on_delete)

    @property
    def is_connected(self) -> bool:
        return self._tcp.is_connected

    # ---------- 动作入口 ----------
    def refresh(self):
        """拉取下位机模型清单"""
        if not self.is_connected:
            self.error_message.emit("未连接下位机，无法获取模型清单")
            return
        self._tcp.request_model_list()

    def load(self, name: str):
        """切换下位机端模型"""
        if not self.is_connected:
            self.error_message.emit("未连接下位机，无法切换模型")
            return
        self._tcp.load_model(name)

    def upload(self, path: str, progress_cb=None):
        """上传本地模型到下位机（.onnx/.pt 自动编译）"""
        if not self.is_connected:
            self.error_message.emit("未连接下位机，无法上传模型")
            return
        self.uploading = True
        self._tcp.upload_model(path, progress_cb)

    def delete(self, name: str):
        """删除下位机端模型（激活模型会被拒绝）"""
        if not self.is_connected:
            self.error_message.emit("未连接下位机，无法删除模型")
            return
        self._tcp.delete_model(name)

    # ---------- 底层信号处理 ----------
    def _on_list(self, payload: dict):
        if payload.get("ok"):
            self.models = payload.get("models", [])
            self.active = payload.get("active", "")
            self.models_updated.emit({"models": self.models, "active": self.active})

    def _on_load(self, payload: dict):
        if payload.get("ok"):
            self.active = payload.get("model", self.active)
        self.load_result.emit(payload)

    def _on_upload(self, payload: dict):
        state = payload.get("state")
        if state in ("saved", "ready", "error"):
            self.uploading = False
        if state == "ready":
            # 编译完成并加载 → 刷新清单
            self.refresh()
        self.upload_state.emit(payload)

    def _on_status(self, payload: dict):
        stage = payload.get("stage")
        if stage in ("export", "compile"):
            self.compiling = True
        elif stage in ("compiled", "error"):
            self.compiling = False
        self.compile_progress.emit(payload)

    def _on_delete(self, payload: dict):
        if payload.get("ok"):
            self.refresh()
        self.delete_result.emit(payload)
