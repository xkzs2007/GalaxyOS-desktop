"""shim: services.xiaoyi_claw_api → real module"""
from services import _load_real
_load_real(__name__)
