"""shim: services.neural_memory_gate → real module"""
from services import _load_real
_load_real(__name__)
