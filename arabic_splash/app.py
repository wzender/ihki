from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).resolve().parent
app = FastAPI()
app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')

@app.get('/')
def home():
    return FileResponse(BASE / 'index.html')
