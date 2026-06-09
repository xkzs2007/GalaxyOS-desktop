"""shim: services.dag_context_manager → real module"""
from services import _load_real
_load_real(__name__)
