import logging
import os
from pathlib import Path

import numpy as np
import json

import torch
import openvino.torch

from diffusers import (
    DiffusionPipeline,
    StableDiffusionXLPipeline,
    StableDiffusionPipeline,
    UNet2DConditionModel,
    LCMScheduler,
)

from ts.handler_utils.timer import timed
from ts.torch_handler.base_handler import BaseHandler

logger = logging.getLogger(__name__)

class StableDiffusionHandler(BaseHandler):
    """
    StableDiffusion handler class for text to image generation.
    """

    def __init__(self):
        super().__init__()
        self.initialized = False

    def initialize(self, ctx):
        """In this initialize function, the Stable Diffusion model is loaded and
        initialized here.
        Args:
            ctx (context): It is a JSON Object containing information
            pertaining to the model artifacts parameters.
        """
        self.context = ctx
        self.manifest = ctx.manifest
        properties = ctx.system_properties
        model_dir = properties.get("model_dir")
        
        self.device = ctx.model_yaml_config["deviceType"]
        
        self.num_inference_steps = ctx.model_yaml_config["handler"]["num_inference_steps"]

        logger.info(f"ctx.model_yaml_config is {ctx.model_yaml_config}")
        logger.info(f"ctx.system_properties is {ctx.system_properties}")
        logger.info(f"Using device={self.device}")

        # Parameters for the model
        compile_unet = ctx.model_yaml_config["handler"]["compile_unet"]
        compile_vae = ctx.model_yaml_config["handler"]["compile_vae"]
        compile_mode = ctx.model_yaml_config["handler"]["compile_mode"]
        change_comp_config = ctx.model_yaml_config["handler"]["change_comp_config"]
        is_xl = ctx.model_yaml_config["handler"]["is_xl"]
        is_lcm = ctx.model_yaml_config["handler"]["is_lcm"]

        compile_options = {}
        pt2_config = ctx.model_yaml_config.get("pt2", {})
        compile_options = {
            "backend": pt2_config.get("backend", "inductor"),
            "options": pt2_config.get("options", {})  # Pass through all options as-is
        }
        logger.info(f"Loading model with PT2 compiler options: {compile_options}")

        # Load model weights
        model_path = Path(ctx.model_yaml_config["handler"]["model_path"])
        ckpt = os.path.join(model_dir, model_path)
    
        """Loads the SDXL LCM pipeline."""

        dtype = torch.float16
        logger.info(f"Loading the SDXL LCM pipeline using dtype: {dtype}")

        if is_lcm:
            unet = UNet2DConditionModel.from_pretrained(f"{ckpt}/lcm/", torch_dtype=dtype)
            pipe = DiffusionPipeline.from_pretrained(ckpt, unet=unet, torch_dtype=dtype)
            pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
            pipe.text_encoder = torch.compile(pipe.text_encoder, **compile_options)
            
        elif is_xl:
            pipe = StableDiffusionXLPipeline.from_pretrained(
                        ckpt, torch_dtype=dtype, use_safetensors=True
                    )
        else:
            pipe = StableDiffusionPipeline.from_pretrained(
                ckpt, torch_dtype=dtype, use_safetensors=True, safety_checker=None
            )

        if compile_unet:
            logger.info("Compiling UNet.")
            if compile_mode == "max-autotune" and change_comp_config:
                pipe.unet.to(memory_format=torch.channels_last)
                torch._inductor.config.conv_1x1_as_mm = True
                torch._inductor.config.coordinate_descent_tuning = True
                torch._inductor.config.epilogue_fusion = False
                torch._inductor.config.coordinate_descent_check_all_directions = True

            pipe.unet = torch.compile(pipe.unet, **compile_options)

        if compile_vae:
            logger.info("Compiling VAE.")
            if compile_mode == "max-autotune" and change_comp_config:
                pipe.vae.to(memory_format=torch.channels_last)
                torch._inductor.config.conv_1x1_as_mm = True
                torch._inductor.config.coordinate_descent_tuning = True
                torch._inductor.config.epilogue_fusion = False
                torch._inductor.config.coordinate_descent_check_all_directions = True

            pipe.vae.decode = torch.compile(pipe.vae.decode, **compile_options)
            
        logger.info(f"Compiled {ckpt} model with {compile_options}")
        pipe.set_progress_bar_config(disable=True)
        
        self.pipeline = pipe
        logger.info(f"Stable Diffusion model loaded successfully: {ckpt}")
        self.initialized = True
            
        return pipe

    @timed
    def preprocess(self, requests):
        """Basic text preprocessing, of the user's prompt.
        Args:
            requests (str): The Input data in the form of text is passed on to the preprocess
            function.
        Returns:
            list : The preprocess function returns a list of prompts.
        """

        assert (
            len(requests) == 1
        ), "Stable Diffusion currently only supported with batch_size=1"

        req_data = requests[0]
        input_data = req_data.get("data") or req_data.get("body")

        if isinstance(input_data, (bytes, bytearray)):
            input_data = input_data.decode("utf-8")

        if isinstance(input_data, str):
            input_data = json.loads(input_data)

        return input_data

    @timed
    def inference(self, model_inputs):
        """Generates the image relevant to the received text.
        Args:
            input_batch (list): List of Text from the pre-process function is passed here
        Returns:
            list : It returns a list of the generate images for the input text
        """
        # Handling inference for sequence_classification.
        guidance_scale = model_inputs.get("guidance_scale") or 5.0
        num_inference_steps = model_inputs.get("num_inference_steps") or 5
        height = model_inputs.get("height") or 768
        width = model_inputs.get("width") or 768
        inferences = self.pipeline(
            model_inputs["prompt"], 
            num_inference_steps=num_inference_steps, 
            guidance_scale=guidance_scale, 
            height=height, 
            width=width
        ).images

        return inferences

    @timed
    def postprocess(self, inference_output):
        """Post Process Function converts the generated image into Torchserve readable format.
        Args:
            inference_output (list): It contains the generated image of the input text.
        Returns:
            (list): Returns a list of the images.
        """
        images = []
        for image in inference_output:
            images.append(np.array(image).tolist())
        return images