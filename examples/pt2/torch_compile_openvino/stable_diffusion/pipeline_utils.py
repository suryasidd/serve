import torch
import openvino.torch
import logging

from diffusers import DiffusionPipeline, DPMSolverMultistepScheduler

logger = logging.getLogger(__name__)
PROMPT = "ghibli style, a fantasy landscape with castles"

def load_pipeline(
    ckpt: str,
    compile_unet: bool,
    compile_vae: bool,
    compile_mode: str,
    change_comp_config: bool,
    compile_options: str
):
    """Loads the SDXL pipeline."""

    dtype = torch.float32
    logger.info(f"Using dtype: {dtype}")
    compile_options_str = ", ".join(
        [f"{k} {v}" for k, v in compile_options.items()]
    )
    logger.info(f"Compiled model with {compile_options_str}")

    if ckpt != "runwayml/stable-diffusion-v1-5":
        pipe = DiffusionPipeline.from_pretrained(ckpt, torch_dtype=dtype, use_safetensors=True)
    else:
        pipe = DiffusionPipeline.from_pretrained(ckpt, torch_dtype=dtype, use_safetensors=True, safety_checker=None)
        # As the default scheduler of SD v1-5 doesn't have sigmas device placement
        # (https://github.com/huggingface/diffusers/pull/6174)
        pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)

    if compile_unet:
        print("Compile UNet.")
        pipe.unet.to(memory_format=torch.channels_last)
        if compile_mode == "max-autotune" and change_comp_config:
            torch._inductor.config.conv_1x1_as_mm = True
            torch._inductor.config.coordinate_descent_tuning = True
            torch._inductor.config.epilogue_fusion = False
            torch._inductor.config.coordinate_descent_check_all_directions = True

        pipe.unet = torch.compile(pipe.unet, **compile_options)

    if compile_vae:
        print("Compile VAE.")
        pipe.vae.to(memory_format=torch.channels_last)
        if compile_mode == "max-autotune" and change_comp_config:
            torch._inductor.config.conv_1x1_as_mm = True
            torch._inductor.config.coordinate_descent_tuning = True
            torch._inductor.config.epilogue_fusion = False
            torch._inductor.config.coordinate_descent_check_all_directions = True

        pipe.vae.decode = torch.compile(pipe.vae.decode, **compile_options)
    logger.info(f"Compiled model with {compile_options_str}")

    pipe.set_progress_bar_config(disable=True)
    return pipe

