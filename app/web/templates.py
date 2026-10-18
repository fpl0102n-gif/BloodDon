"""Jinja2 template engine configuration for server-rendered web pages.

Per:
- docs/05-architecture.md §4 (web/templates structure)
- docs/11-decisions.md §3 (D-34, D-70, D-71, D-73)
- docs/07-test-cases.md §5.16 (T-WEB-*)
"""

from pathlib import Path

from starlette.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Initialize Jinja2Templates with explicit autoescape enabled for strict XSS prevention (D-70)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.autoescape = True
