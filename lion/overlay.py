from PIL import Image, ImageDraw
from shape import lion_px, eye_px
import os
from pathlib import Path
# Reference photo of the lion (third-party image, NOT in the repository; lion/reference.jpg is git-ignored).
# Point LION_REFERENCE_IMAGE at another file to override.
REFERENCE = Path(os.environ.get("LION_REFERENCE_IMAGE", Path(__file__).resolve().parent / "reference.jpg"))
im = Image.open(REFERENCE).convert('RGB')
im = im.resize((im.width*2, im.height*2))
d = ImageDraw.Draw(im)
g = lion_px(); print(g.geom_type, g.is_valid)
for p in ([g] if g.geom_type=='Polygon' else list(g.geoms)):
    d.line([(x*2,y*2) for x,y in p.exterior.coords], fill=(255,0,0), width=3)
d.line([(x*2,y*2) for x,y in eye_px().exterior.coords], fill=(0,0,255), width=3)
im.save('overlay.png')
