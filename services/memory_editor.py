"""shim: services.memory_editor → real module"""
from services import _load_real
_load_real(__name__)
