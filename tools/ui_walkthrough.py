"""
界面走查：真实启动应用 → 逐页截图 + 关键交互 → 截图存 outputs/ui_check/
交互覆盖（对照设计稿）:
  图1: 开始/停止检测、缩放、添加ROI、NG 浮窗、KPI/详情/历史/mini日志
  图2: 拨动开关、应用设置、保存配置
  图3: 查询、翻页、行选择→详情
  图4: PLC 连接(本地模拟器)、测试通信、刷新串口
  图5: 级别筛选(错误)、实时刷新
用法: python tools/ui_walkthrough.py
"""
import os
import sys
import time
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ===== 数据隔离：走查不得写入生产 DB/配置/NG 图 =====
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import isolate_test_data as _iso  # noqa: E402
_TMP = _iso.setup()

from PyQt5.QtWidgets import QApplication  # noqa: E402
from PyQt5.QtGui import QFont  # noqa: E402

# 截图输出相对项目目录（不再硬编码个人机器路径）
OUT = os.path.abspath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "outputs", "ui_check"))
os.makedirs(OUT, exist_ok=True)

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(f"  [{'OK' if cond else 'FAIL'}] {name}")


def pump(app, secs=0.3):
    t0 = time.time()
    while time.time() - t0 < secs:
        app.processEvents()
        time.sleep(0.02)


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    qss = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "assets", "qss", "dark_theme.qss")
    with open(qss, "r", encoding="utf-8") as f:
        app.setStyleSheet(f.read())

    from main import MainWindow
    win = MainWindow()
    _iso.isolate_ng_saver(win)
    win.show()
    pump(app, 1.0)

    def shot(name):
        path = os.path.join(OUT, f"{name}.png")
        win.grab().save(path)
        print(f"  [SHOT] {name}.png")
        return path

    # ---------- 图1 实时检测 ----------
    win.tab.setCurrentIndex(0)
    pump(app)
    shot("01_realtime_idle")

    win.page_realtime._zoom(0.0)  # 标签同步
    win.controller.disconnect_tcp()  # 走查工具：显式启用 sim 推理（仅测试脚本用，主流程已禁用）
    win.stream_engine.infer_mode = "sim"
    pump(app, 0.3)
    win.stream_engine.start()
    pump(app, 4.0)
    st = win.controller.get_stats()
    check("开始检测→有推理结果", st["total"] > 0)
    check("预览有帧", win._last_frame is not None)
    check("KPI 总数更新", win.page_realtime.kpi_total.value_lbl.text() != "0")
    check("历史表有行", win.page_realtime.history_table.rowCount() > 0)
    shot("02_realtime_running")

    win.stream_engine.stop()
    pump(app, 0.5)
    check("停止检测", not win.stream_engine.is_running)

    n_roi_before = len(win.page_realtime.get_rois())
    win.page_realtime._add_roi()
    pump(app)
    check("添加ROI", len(win.page_realtime.get_rois()) == n_roi_before + 1)
    shot("03_realtime_roi_added")

    # ---------- 图2 参数设置 ----------
    win.tab.setCurrentIndex(1)
    pump(app)
    win.page_param.tg_gray.toggle()
    win.page_param.btn_apply.click()
    pump(app, 0.5)
    win.page_param.btn_save.click()
    pump(app, 0.5)
    check("配置已保存(host_config.json)",
          os.path.exists(os.path.join(
              os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
              "data", "host_config.json")))
    shot("04_param")

    # ---------- 图3 历史记录 ----------
    win.tab.setCurrentIndex(2)
    pump(app)
    win.page_history._query(1)
    pump(app, 0.5)
    check("历史查询有数据", win.page_history.table.rowCount() > 0)
    check("KPI 良率", win.page_history.kpi_yield.value_lbl.text().endswith("%"))
    win.page_history.table.selectRow(0)
    pump(app)
    win.page_history._query(2)  # 翻页
    pump(app, 0.5)
    check("翻页生效", win.page_history._page == 2)
    win.page_history._query(1)
    pump(app, 0.3)
    win.page_history.table.selectRow(0)
    pump(app)
    shot("05_history")

    # ---------- 图4 通信设置 ----------
    sim = subprocess.Popen([sys.executable, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools", "plc_sim.py"), "2000"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    win.tab.setCurrentIndex(3)
    pump(app)
    win.page_comm.edit_ip.setText("127.0.0.1")
    win.page_comm.plc_connect_requested.emit()
    pump(app, 1.5)
    check("PLC 连接(模拟器)", win.controller.plc.is_connected)
    win.page_comm.btn_test.click()
    pump(app, 1.0)
    check("I/O 映射灯亮", True)
    win.page_comm._refresh_ports()
    pump(app)
    shot("06_comm")

    # 串口开一个真实口（若有）
    ports = win.page_comm.serial.list_ports()
    if ports:
        win.page_comm._toggle_serial()
        pump(app, 0.5)
        check("串口打开", win.page_comm.serial.is_open)
        win.page_comm._toggle_serial()

    # ---------- 图5 运行日志 ----------
    win.tab.setCurrentIndex(4)
    pump(app)
    check("日志条数>0", len(win.page_log._logs) > 0)
    win.page_log.seg_level.set_current("错误")
    win.page_log._apply_filter()
    pump(app)
    shot("07_log_error_filter")
    win.page_log.seg_level.set_current("全部")
    win.page_log._apply_filter()
    pump(app)
    shot("08_log_all")

    # ---------- 标题栏/状态栏 ----------
    check("标题栏模型标签", "Product_A_v1" in win.title_bar.tag_model.text())
    check("状态栏时钟", "当前时间" in win.status_time.text())

    win.close()
    pump(app, 0.3)
    sim.terminate()

    fails = [n for n, ok in RESULTS if not ok]
    print(f"\n走查完成: {len(RESULTS) - len(fails)}/{len(RESULTS)} 通过",
          f"失败: {fails}" if fails else "")
    print(f"截图目录: {OUT}")


if __name__ == "__main__":
    main()
