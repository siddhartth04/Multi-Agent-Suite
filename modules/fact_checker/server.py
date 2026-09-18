"""ASGI entrypoint for the fact_checker service."""

from common.service import create_service
from modules.fact_checker.module import DEFINITION

app = create_service(DEFINITION)
