"""ASGI entrypoint for the marketing service."""

from common.service import create_service
from modules.marketing.module import DEFINITION

app = create_service(DEFINITION)
