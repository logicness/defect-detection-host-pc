"""pages 包：5 个页面"""
from pages.realtime_detect import RealtimeDetectPage
from pages.param_setting import ParamSettingPage
from pages.history_record import HistoryRecordPage
from pages.comm_setting import CommSettingPage
from pages.run_log import RunLogPage

__all__ = ["RealtimeDetectPage", "ParamSettingPage", "HistoryRecordPage",
           "CommSettingPage", "RunLogPage"]
