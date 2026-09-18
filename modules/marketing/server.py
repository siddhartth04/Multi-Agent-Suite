from common.service import create_service
from .app import run
app = create_service("Marketing Service", "Researcher + Strategist + Writer", run, "product")
