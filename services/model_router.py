"""shim: services.model_router → real module"""
from services import _load_real
_load_real(__name__)
