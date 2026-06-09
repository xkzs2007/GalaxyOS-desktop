"""shim: services.rag_optimizer → real module"""
from services import _load_real
_load_real(__name__)
