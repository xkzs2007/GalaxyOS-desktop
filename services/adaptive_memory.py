"""shim: services.adaptive_memory → real module"""
from services import _load_real
_load_real(__name__)
