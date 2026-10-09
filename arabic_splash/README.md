# Animated splash for FastAPI / HTML

Run locally:

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 7860
```

For Hugging Face Spaces Docker, run uvicorn on port 7860 and expose port 7860 in your Dockerfile.

To integrate into an existing project: copy static/logo.png, splash.css, splash.js; add the stylesheet to your HTML head, the `#app-splash` div from index.html at the beginning of body, and the JS script at the end of body. Ensure your FastAPI app serves /static. The demo uses three clipped copies of the original image, preserving the logo exactly. It is a reveal animation, not true vector stroke tracing.
