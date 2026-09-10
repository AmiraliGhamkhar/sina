import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.templates import PromptTemplate
from app.core.config import settings


QUALITY_TAGS = "masterpiece, high quality, very aesthetic, 4k" 

HUMAN_TAGS= "(realistic skin:1.2), pores"

POLAROID_TAGS= "shot on polaroid, shot on kodak, candid photo, (polaroid pic:1.4)"


DEFAULT_STRUCTURE = """
quality,
art style, artist references,
camera,
subject,
accessories and clothes,
pose,
environment,
lighting
"""

async def load_rewrite_model() -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{settings.LM_URL}/api/v1/models/load",
            json={
                "model": settings.LM_DEFAULT_MODEL,
                "context_length": 2048,
                "flash_attention": True
            }
        )
        response.raise_for_status()
        return response.json()["instance_id"]

async def unload_rewrite_model(instance_id: str):
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(
            f"{settings.LM_URL}/api/v1/models/unload",
            json={"instance_id": instance_id}
        )

async def get_template(template_id: str, user_id: str, db: AsyncSession) -> str:
    if not template_id:
        return DEFAULT_STRUCTURE
    result = await db.execute(
        select(PromptTemplate).where(
            PromptTemplate.id == template_id,
            PromptTemplate.user_id == user_id,
        )
    )
    template = result.scalar_one_or_none()
    return template.structure if template else DEFAULT_STRUCTURE

async def rewrite_prompt(prompt: str, template_id: str = None, db: AsyncSession = None, user_id: str = None) -> str:
    structure = await get_template(template_id, user_id, db) if db else DEFAULT_STRUCTURE

    instance_id = await load_rewrite_model()

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{settings.LM_URL}/v1/chat/completions",
                json={
                    "model": settings.LM_DEFAULT_MODEL,
                    "messages": [
                        {
                            "role": "system",
"content": f"""You are an SDXL prompt formatter.Convert natural language user requests into concise 
comma-separated Stable Diffusion XL prompt tags. Ensure the output covers everything the user had requested for. 
After generation, compare output with user input to improve prompt if anything's missing. Do not use more than 3 words per tag
Also dont be shy to use a little imagination.

Follow this category order:
{structure}


WEIGHTING RULES:
- If the user emphasizes, strongly requests, or explicitly highlights a feature, increase its prompt weight
- Weights MUST be between 1.3 and 1.5. NO LESS NO MORE.

WEIGHT GUIDELINES:
- Mild emphasis: 1.4
- Clear importance: 1.5
- Strong emphasis : 1.6
- Critical/explicit requirement: 1.8

Apply weights when the user says phrases like:
- "make sure"
- "important"
- "must have"
- "ensure"
- "focus on"
- "especially"
- "emphasize"
- "with prominent"
- repeated mentions
- synonyms of the words listed above 


Never overuse weighting.
Only weight the specifically emphasized feature. NEVER EXCEED 1.8 WEIGHT threshold. NEVER GO PAST 1.8 WEIGHT

EXAMPLE INPUT:
"A lone samurai standing in the rain at night. Make sure he has a fire emblem on his clothing."

EXAMPLE OUTPUT:
high quality, 4k, masterpiece, depth of field, detailed face, ultra detailed, lone samurai, standing, wet clothes, (fire emblem on clothing:1.4), katana, rain, night, moody atmosphere, dark environment, cinematic lighting, neon reflections, volumetric lighting, 

The weight must be enclosed in the bracket, followed by the tag. In the example given, "fire emblem on clothing" is the tag and 1.4 is the weight. so for the tags you think are important to the user's request, use the following format: (tag:weight). The weight MUST be INSIDE the bracket. 

One request can have multiple weighted tags. 
EXAMPLE INPUT: goth girl with big boobs kneeling infront of a mirror, make sure she is thick as fuck.

EXAMPLE OUTPUT: 
4k, masterpiece, realistic skin, goth girl, (big boobs:1.4), large bust, (mirror selfie:1.3), (voluptuous body:1.8), gothic clothing, metallic accessories, dark lighting, moody atmosphere, 


Notice how i expanded on the user prompt without breaking the original intent. That's what you're supposed to do for user inputs


RULES:
- Output ONLY the final prompt
- No explanations
- No JSON
- No markdown
- No full sentences
- No numbering
- Preserve all user intent
- Focus on visual descriptors


"""
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    "stream": False,
                    "temperature": 0.2,
                    "top_k": 90,
                    "top_p": 0.95,
                    "min_p": 0.09,
                    "repeat_penalty": 1.1,
                }
            )
            response.raise_for_status()
            rewritten= response.json()["choices"][0]["message"]["content"].strip()
            return f"{rewritten}"
    finally:
        await unload_rewrite_model(instance_id)