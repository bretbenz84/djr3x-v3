"""Export V2's MobileCLIP-S2 image encoder to ONNX for V3 room recognition.

Build-time only. V3 never imports torch/open_clip for rooms: it runs the exported file with
onnxruntime. Run with an interpreter that has torch, open_clip and onnx (V2's venv works; it
is used read-only and nothing in V2 is changed):

    /Users/bbenziger/djr3x-v2/venv/bin/python -I tools/export_place_model.py \
        --cache /Users/bbenziger/djr3x-v2/assets/models/mobileclip \
        --out local/models/places/mobileclip-s2.onnx

The graph is `encode_image` exactly as V2 calls it (no reparameterization), so embeddings stay
comparable with V2's stored `mobileclip_s2_v1` vectors. Input: float32 1x3x256x256 RGB in [0,1].
Output: the raw 512-d embedding (callers L2-normalize). See docs/ROOM_RECOGNITION.md.
"""
import argparse
from hashlib import sha256
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    import open_clip
    import torch
    model, _, _ = open_clip.create_model_and_transforms('MobileCLIP-S2', pretrained='datacompdr',
                                                        cache_dir=str(args.cache))
    model.eval()

    class Encoder(torch.nn.Module):
        def __init__(self, clip):
            super().__init__()
            self.clip = clip

        def forward(self, pixels):
            return self.clip.encode_image(pixels)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with torch.no_grad():
        torch.onnx.export(Encoder(model), torch.zeros(1, 3, 256, 256), str(args.out),
                          input_names=['pixels'], output_names=['embedding'], opset_version=17,
                          dynamo=False)
    print(args.out, sha256(args.out.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
