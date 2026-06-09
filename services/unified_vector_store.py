"""shim: services.unified_vector_store → real module"""
from services import _load_real
_load_real(__name__)
