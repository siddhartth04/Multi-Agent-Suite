from common.service import create_service
from .app import run
app = create_service("Travel Service", "Planner + Search + Booking Advisor", run, "travel request")
