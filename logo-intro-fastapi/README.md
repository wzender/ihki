# SVG logo introduction — FastAPI / HTML

The ornament is traced from the supplied raster image into SVG centerline paths, animated with stroke-dashoffset. This is real SVG line drawing, not a mask over the image. Raster tracing retains slight pixel irregularities; an original vector logo would improve precision further. Original calligraphy and Hebrew lettering are embedded unchanged as a cropped PNG.

Timing: ornament 0–2 seconds; lettering 1.55–2.05 seconds; fade 2.65–3.00 seconds. No animation libraries, external requests, or Python processing at runtime. Reduced-motion users skip the splash. The intro does not wait for model loading or block the main interface.

## Preview
Open index.html locally, or run:

    pip install -r requirements.txt
    uvicorn app:app --host 0.0.0.0 --port 7860

## Existing FastAPI project
Copy static/logo-intro.svg into your static directory. Copy the #logo-splash CSS, reduced-motion rule, splash div and cleanup script from index.html into your existing page. Keep your existing main interface. Mount /static only if it is not mounted already. Adjust the image URL if the application uses a URL prefix. No changes to your model or backend are required.

## Hugging Face Docker Space
Use your existing Docker setup and serve FastAPI on 0.0.0.0:7860. The example below can run this standalone demo. The animation starts after the HTML loads; it does not cover a Space cold start before the server responds.
