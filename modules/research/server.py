"""ASGI entrypoint for the research service."""

from common.service import create_service
from modules.research.module import DEFINITION

app = create_service(DEFINITION)
