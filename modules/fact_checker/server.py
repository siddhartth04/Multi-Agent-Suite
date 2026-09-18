from common.service import create_service
from .app import run
app = create_service("Fact Checker Service", "Researcher + Verification Agent", run, "claim")
