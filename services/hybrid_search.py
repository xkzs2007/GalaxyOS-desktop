"""shim: services.hybrid_search → real module"""
from services import _load_real
_load_real(__name__)
