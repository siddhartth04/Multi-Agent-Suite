from common.service import create_service
from .app import run
app = create_service("Research Service", "Researcher + Reviewer", run, "topic")
