"""ASGI entrypoint for the travel service."""

from common.service import create_service
from modules.travel.module import DEFINITION

app = create_service(DEFINITION)
