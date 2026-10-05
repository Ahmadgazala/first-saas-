import sys
from PIL import Image, ImageDraw
import os
from pathlib import Path
# Reference photo of the lion (third-party image, NOT in the repository; lion/reference.jpg is git-ignored).
# Point LION_REFERENCE_IMAGE at another file to override.
REFERENCE = Path(os.environ.get("LION_REFERENCE_IMAGE", Path(__file__).resolve().parent / "reference.jpg"))
im = Image.open(REFERENCE).convert('RGB')
def crop(name, x0,y0,x1,y1,s=5,step=10):
    c = im.crop((x0,y0,x1,y1)).resize(((x1-x0)*s,(y1-y0)*s), Image.LANCZOS)
    d = ImageDraw.Draw(c)
    for x in range((x0//step)*step, x1+1, step):
        if x<x0: continue
        X=(x-x0)*s; major = x%50==0
        d.line([(X,0),(X,c.height)], fill=(255,0,0) if major else (255,255,0), width=1)
        if major: d.text((X+2,2), str(x), fill=(255,255,255))
    for y in range((y0//step)*step, y1+1, step):
        if y<y0: continue
        Y=(y-y0)*s; major = y%50==0
        d.line([(0,Y),(c.width,Y)], fill=(255,0,0) if major else (255,255,0), width=1)
        if major: d.text((2,Y+2), str(y), fill=(255,255,255))
    c.save(name)
crop('g_head.png',165,35,365,255,5)
crop('g_front.png',200,190,360,345,6)
crop('g_rear.png',390,80,559,345,4)
crop('g_mid.png',290,80,480,240,5)
