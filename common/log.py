import logging
import os
import sys

_LOG_FMT = logging.Formatter(
    "[%(levelname)s][%(asctime)s][%(filename)s:%(lineno)d] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _reset_logger(log):
    for handler in log.handlers:
        handler.close()
        log.removeHandler(handler)
        del handler
    log.handlers.clear()
    log.propagate = False

    # Always attach a console handler so stdout shows logs.
    console_handle = logging.StreamHandler(sys.stdout)
    console_handle.setFormatter(_LOG_FMT)
    log.addHandler(console_handle)

    # Only add a FileHandler when stdout is NOT already redirected to run.log.
    # When the user runs `python3 app.py >> run.log 2>&1`, the console handler
    # already writes to run.log via stdout, so adding a second FileHandler
    # would duplicate every log line inside the file.
    _add_file_handler = True
    try:
        stdout_path = os.readlink(f"/proc/{os.getpid()}/fd/1")
        if "run.log" in stdout_path:
            _add_file_handler = False
    except Exception:
        pass

    if _add_file_handler:
        file_handle = logging.FileHandler("run.log", encoding="utf-8")
        file_handle.setFormatter(_LOG_FMT)
        log.addHandler(file_handle)


def _get_logger():
    log = logging.getLogger("log")
    _reset_logger(log)
    log.setLevel(logging.INFO)
    return log


# 日志句柄
logger = _get_logger()
