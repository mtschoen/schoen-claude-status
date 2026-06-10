"""Shared state-save/restore helpers for verify_walker*.py scripts."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import statusline_lib.walker as walker_module


def save_walker_state():
    return {
        "shutil_which": walker_module.shutil.which,
        "os_path_isfile": walker_module.os.path.isfile,
        "os_path_isdir": walker_module.os.path.isdir,
        "os_path_realpath": walker_module.os.path.realpath,
        "subprocess_run": walker_module.subprocess.run,
        "environ": dict(os.environ),
        "roots_config_path": walker_module._WALKER_ROOTS_CONFIG_PATH,
        "os_path_expanduser": walker_module.os.path.expanduser,
    }


def restore_walker_state(state):
    walker_module.shutil.which = state["shutil_which"]
    walker_module.os.path.isfile = state["os_path_isfile"]
    walker_module.os.path.isdir = state["os_path_isdir"]
    walker_module.os.path.realpath = state["os_path_realpath"]
    walker_module.subprocess.run = state["subprocess_run"]
    walker_module.os.path.expanduser = state["os_path_expanduser"]
    for key in list(os.environ.keys()):
        if key not in state["environ"]:
            del os.environ[key]
    for key, value in state["environ"].items():
        os.environ[key] = value
    walker_module._WALKER_ROOTS_CONFIG_PATH = state["roots_config_path"]
