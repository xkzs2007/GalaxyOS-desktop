"""shim: services.chain_of_verification → real module"""
from services import _load_real
_load_real(__name__)
