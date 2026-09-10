import httpx
import uuid
import copy
import logging
from urllib.parse import urlencode
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.workflows import Workflow
from app.core.exceptions import NotFoundError
from app.core.config import settings


logger = logging.getLogger(__name__)

COMFY_URL = settings.COMFY_URL

# Valid aspect_ratio values accepted by the ComfyUI ResolutionSelector node (id "17").
# Single source of truth: the frontend fetches these via GET /v1/images/aspect-ratios,
# and the image request validates against them. Every workflow that uses the
# ResolutionSelector node shares this list.
ASPECT_RATIOS = [
    "1:1 (Square)",
    "3:2 (Photo)",
    "4:3 (Standard)",
    "16:9 (Widescreen)",
    "21:9 (Ultrawide)",
    "2:3 (Portrait Photo)",
    "3:4 (Portrait Standard)",
    "9:16 (Portrait Widescreen)",
]
DEFAULT_ASPECT_RATIO = "9:16 (Portrait Widescreen)"

BASE_WORKFLOW = {
    "3": {
        "inputs": {
            "seed": 685468484323813,
            "steps": 10,
            "cfg": 1.2,
            "sampler_name": "lcm",
            "scheduler": "sgm_uniform",
            "denoise": 1,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["18", 0]
        },
        "class_type": "KSampler"
    },
    "4": {
        "inputs": {"ckpt_name": "xxxRay_dmd2.safetensors"},
        "class_type": "CheckpointLoaderSimple"
    },
    "6": {
        "inputs": {
            "text": "",
            "clip": ["4", 1]
        },
        "class_type": "CLIPTextEncode"
    },
    "7": {
        "inputs": {
            "text": "text, watermark, blurry, low quality",
            "clip": ["4", 1]
        },
        "class_type": "CLIPTextEncode"
    },
    "8": {
        "inputs": {
            "samples": ["3", 0],
            "vae": ["4", 2]
        },
        "class_type": "VAEDecode"
    },
    "17": {
        "inputs": {
            "aspect_ratio": DEFAULT_ASPECT_RATIO,
            "megapixels": 1.3
        },
        "class_type": "ResolutionSelector"
    },
    "18": {
        "inputs": {
            "width": ["17", 0],
            "height": ["17", 1],
            "batch_size": 1
        },
        "class_type": "EmptySD3LatentImage"
    },
    "19": {
        "inputs": {"images": ["8", 0]},
        "class_type": "PreviewImage"
    }
}

async def get_workflow(workflow_id: str, user_id: str, db: AsyncSession):
    if not workflow_id:
        return BASE_WORKFLOW, None
    request= await db.execute(select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.user_id == user_id))
    
    workflow= request.scalar_one_or_none()
    if workflow is None:
        raise NotFoundError("Workflow not found")
    return workflow.graph, workflow.param_map
    

# find the first node whose class_type contains X (here we use a substring match) 
def _find_node(graph, class_substr):
    for node_id, node in graph.items():
        if class_substr in node.get("class_type", ""):
            return node_id, node
    return None, None

# exact equality variant of _find_node — critical anchors must never be matched
# by substring, or a KSamplerAdvanced gets silently treated as a KSampler (R5)
def _find_node_exact(graph, class_name: str) -> tuple[str | None, dict | None]:
    for node_id, node in graph.items():
        if node.get("class_type", "") == class_name:
            return node_id, node
    return None, None


# Anchor patterns that MUST be unambiguous at generation time: auto-injection
# can only target one node per parameter, so a graph with more than one
# candidate needs an explicit param_map or the wrong node gets the values.
# KSampler and KSamplerAdvanced are the same anchor family — a graph with both
# can't be auto-injected safely either.
_CRITICAL_ANCHOR_PATTERNS = (
    ("KSampler", lambda ct: ct in ("KSampler", "KSamplerAdvanced")),
    ("ResolutionSelector", lambda ct: ct == "ResolutionSelector"),
    ("LatentImage", lambda ct: ct.endswith("LatentImage")),
)


def validate_workflow_anchors(graph: dict, param_map: dict | None) -> None:
    """Raise ValueError when a critical anchor pattern has more than one match
    and param_map doesn't explicitly target one of the matched node ids.

    Called at workflow upload/update so ambiguity is caught before a user ever
    generates from the graph; the 422 tells them to set param_map.
    """
    mapped_node_ids = {
        target[0]
        for target in (param_map or {}).values()
        if isinstance(target, (list, tuple)) and len(target) >= 1
    }
    for label, matcher in _CRITICAL_ANCHOR_PATTERNS:
        matches = [nid for nid, node in graph.items()
                   if matcher(node.get("class_type", ""))]
        if len(matches) > 1 and not mapped_node_ids.intersection(matches):
            raise ValueError(
                f"ambiguous '{label}' anchors: {len(matches)} nodes match "
                f"({', '.join(sorted(matches))}); set param_map to target the "
                "intended node"
            )

def inject_params(
    graph: dict,
    param_map: dict | None = None,
    *,
    prompt: str,
    negative_prompt: str,
    steps: int,
    cfg: float,
    seed: int,
    aspect_ratio: str,
    batch_size: int,
) -> dict:
    
    g= copy.deepcopy(graph)
    targets= {}

    # auto-detect: to figure out which node + input each param maps to.
    # Anchor on the sampler; steps/cfg/seed sit right on it. Exact match only —
    # a KSamplerAdvanced must never be treated as the KSampler anchor (R5).
    # With 0 or >1 matches we don't guess: the param stays unset unless
    # param_map explicitly overrides it.
    sampler_ids = [nid for nid, node in g.items()
                   if node.get("class_type", "") == "KSampler"]
    if len(sampler_ids) == 1:
        sampler_id = sampler_ids[0]
        sampler = g[sampler_id]
        s_inputs = sampler["inputs"]
        targets["steps"] = [sampler_id, "steps"]
        targets["cfg"] = [sampler_id, "cfg"]
        # KSampler calls it "seed"; KSamplerAdvanced calls it "noise_seed"
        targets["seed"] = [sampler_id, "noise_seed" if "noise_seed" in s_inputs else "seed"]
        # positive/negative are links like ["node_id", "input_slot"] and
        # the prompt text lives on that node's "text"
        if "positive" in s_inputs:
            targets["positive"] = [s_inputs["positive"][0], "text"]
        if "negative" in s_inputs:
            targets["negative"] = [s_inputs["negative"][0], "text"]

    # aspect ratio + batch size have their own nodes; again, don't guess when
    # the anchor is missing or ambiguous
    res_ids = [nid for nid, node in g.items()
               if node.get("class_type", "") == "ResolutionSelector"]
    if len(res_ids) == 1:
        targets["aspect_ratio"] = [res_ids[0], "aspect_ratio"]

    latent_ids = [nid for nid, node in g.items()
                  if node.get("class_type", "").endswith("LatentImage")]
    if len(latent_ids) == 1:
        targets["batch_size"] = [latent_ids[0], "batch_size"]

    # explicit overrides from the workflow's param_map have priority
    # over auto-detect
    targets.update(param_map or {})
    values= {
        "positive": prompt,
        "negative": negative_prompt,
        "steps": steps,
        "cfg": cfg,
        "seed": seed,
        "aspect_ratio": aspect_ratio,
        "batch_size": batch_size}
    
    for param, value in values.items():
        target = targets.get(param)
        if not target:
            continue                      # if this graph has no slot for that param then skip, don't crash
        node_id, input_key = target       # target is [node_id, input_slot]
        if node_id in g and "inputs" in g[node_id]:
            g[node_id]["inputs"][input_key] = value

    return g


async def generate_image(
    workflow_id: str | None,
    user_id: str,
    db: AsyncSession,
    *,
    prompt: str,
    negative_prompt: str,
    steps: int,
    cfg: float,
    aspect_ratio: str,
    batch_size: int,
    seed: int | None = None,
) -> str:
    
    graph, param_map = await get_workflow(workflow_id, user_id, db)
    seed = seed if seed is not None else uuid.uuid4().int % (2**32)

    workflow = inject_params(
        graph,
        param_map,
        prompt=prompt,
        negative_prompt=negative_prompt,
        steps=steps,
        cfg=cfg,
        seed=seed,
        aspect_ratio=aspect_ratio,
        batch_size=batch_size,
    )

    client_id = str(uuid.uuid4())

    async with httpx.AsyncClient(timeout=120) as client:
        # submit the job
        response = await client.post(
            f"{COMFY_URL}/prompt",
            json={"prompt": workflow, "client_id": client_id}
        )
        response.raise_for_status()
        prompt_id = response.json()["prompt_id"]

    return prompt_id


async def get_job_status(prompt_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{COMFY_URL}/history/{prompt_id}")
        response.raise_for_status()
        history = response.json()
        
        if prompt_id not in history:
            return {"status": "pending"}

        job = history[prompt_id]

        # surface execution failures (OOM, missing checkpoint, bad node)
        # instead of leaving the client polling a job that will never finish
        status_info = job.get("status", {}) or {}
        if status_info.get("status_str") == "error":
            error_msg = None
            for entry in status_info.get("messages", []):
                # messages are [type, payload] pairs
                if isinstance(entry, (list, tuple)) and len(entry) == 2 and entry[0] == "execution_error":
                    error_msg = entry[1].get("exception_message")
                    break
            return {"status": "failed", "error": error_msg or "ComfyUI execution error"}

        outputs = job.get("outputs", {})
        
        # find images in outputs
        images = []
        for node_id, node_output in outputs.items():
            if "images" in node_output:
                for img in node_output["images"]:
                    images.append({
                        "filename": img["filename"],
                        # relative on purpose (S3): the client fetches the file
                        # from the authed gateway route, never ComfyUI directly
                        "url": "/v1/images/file?" + urlencode({"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": img.get("type", "output")})
                    })
        
        return {"status": "complete", "images": images}