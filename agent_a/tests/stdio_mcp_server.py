"""
A Role-1-style MCP server run as a real subprocess over stdio, for the
wire-level test. It serves the backend in-process against CRM_DB_PATH (the
same SQLite file the test process uses).
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1] / "backend")]

from app.main import app  # noqa: E402
from fake_mcp import filter_style_server  # noqa: E402

if __name__ == "__main__":
    filter_style_server(app).run("stdio")
