from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
app = FastAPI()
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

@app.get("/")
def index():
    return FileResponse(ROOT / "index.html")
