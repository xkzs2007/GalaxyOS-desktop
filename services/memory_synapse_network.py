"""shim: services.memory_synapse_network → real module"""
from services import _load_real
_load_real(__name__)
