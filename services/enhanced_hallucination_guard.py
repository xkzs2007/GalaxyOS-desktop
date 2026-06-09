"""shim: services.enhanced_hallucination_guard → real module"""
from services import _load_real
_load_real(__name__)
