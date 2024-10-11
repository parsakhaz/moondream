import torch
import argparse
import os

from PIL import Image
from transformers import AutoTokenizer
from moondream.hf import Moondream

from .weights import load_from_safetensors
from .vision import encode_image
from .text import text_encoder, text_decoder
from .rope import precompute_freqs_cis

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", "-i", type=str, required=True)
    parser.add_argument("--prompt", "-p", type=str, required=True)
    parser.add_argument("--model", "-m", type=str, required=True)
    parser.add_argument("--max-tokens", "-t", type=int, default=200)
    parser.add_argument("--sampler", "-s", type=str, default="greedy")
    args = parser.parse_args()

    if torch.cuda.is_available():
        torch.set_default_device("cuda")
    elif torch.backends.mps.is_available():
        torch.set_default_device("mps")

    # Load model.
    model_path = args.model
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model not found at {model_path}")
    model = load_from_safetensors(model_path)

    # Encode image.
    image_path = args.image
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found at {image_path}")
    image = Image.open(image_path)
    image = image.resize((378, 378))
    image_tensor = encode_image(image, model.vision)

    # Encode text, and create inputs_embeds.
    tokenizer = AutoTokenizer.from_pretrained("vikhyatk/moondream2")
    prompt = f"\n\nQuestion: {args.prompt}\n\nAnswer:"
    input_ids = tokenizer(prompt, return_tensors="pt")["input_ids"]
    input_ids = torch.cat([torch.tensor([[tokenizer.eos_token_id]]), input_ids], dim=1)
    inputs_embeds = text_encoder(input_ids, model.text)
    inputs_embeds = torch.cat(
        [
            inputs_embeds[:, 0:1, :],
            image_tensor.unsqueeze(0),
            inputs_embeds[:, 1:, :],
        ],
        dim=1,
    )

    kv_cache = {}
    freqs_cis = precompute_freqs_cis(32, 2048)

    for _ in range(args.max_tokens):
        with torch.no_grad():
            logits, kv_cache = text_decoder(
                inputs_embeds, model.text, kv_cache, freqs_cis
            )

            if args.sampler == "multinomial":
                next_token = torch.multinomial(
                    torch.softmax(logits, dim=-1), num_samples=1
                ).squeeze(0)
            elif args.sampler == "greedy":
                next_token = torch.argmax(logits, dim=-1)
            else:
                raise ValueError(f"Invalid sampler: {args.sampler}")

            if next_token == tokenizer.eos_token_id:
                print()
                break

            input_ids = next_token.unsqueeze(0)
            inputs_embeds = text_encoder(input_ids, model.text)
            # print(inputs_embeds.shape)

            output_text = tokenizer.batch_decode(input_ids)[0]
            print(output_text, end="", flush=True)
