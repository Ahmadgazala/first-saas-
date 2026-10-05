# Third-party code

## TripoSR (MIT License, Copyright (c) 2024 Tripo AI & Stability AI)

Source: https://github.com/VAST-AI-Research/TripoSR - vendored without examples, figures, the
Gradio demo, the CLI (`run.py`) and texture baking (`tsr/bake_texture.py`, which needs OpenGL). Used by `app/reconstruction/triposr.py` to turn a single photo into a 3D mesh.

Local modification: `tsr/models/isosurface.py` uses scikit-image's marching cubes instead of the
`torchmcubes` CUDA/C++ extension (same output convention), so it installs everywhere with pip.

Model weights (`stabilityai/TripoSR`, ~1.7 GB) are downloaded from Hugging Face on first use and
cached in the Hugging Face cache directory; they are not part of this repository.
