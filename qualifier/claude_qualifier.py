from anthropic import Anthropic
import json
import os
from typing import Dict, Tuple, Optional
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


class ClaudeLeadQualifier:
    """Claude AI-powered lead scoring with HOT/WARM/COLD classification."""
    
    def __init__(self, api_key: str = ANTHROPIC_API_KEY):
        self.client = Anthropic(api_key=api_key)
        self.model = "claude-sonnet-4-6"
    
    def qualify_lead(self, lead_data: Dict, language: str = "english") -> Tuple[str, str, str]:
        """
        Qualify a lead using Claude AI.
        
        Args:
            lead_data: Dictionary containing lead information (name, email, phone, property_url, property_address, raw_data)
            language: Language for analysis (english, spanish)
        
        Returns:
            Tuple of (score: HOT/WARM/COLD, reasoning: str, analysis: str)
        """
        
        # Build lead description
        lead_desc = self._format_lead_for_analysis(lead_data)
        
        # Language-specific prompt
        if language.lower() == "spanish":
            system_prompt = self._get_spanish_system_prompt()
            user_prompt = f"Por favor, califica este lead inmobiliario:\n\n{lead_desc}"
        else:
            system_prompt = self._get_english_system_prompt()
            user_prompt = f"Please qualify this real estate lead:\n\n{lead_desc}"
        
        # Get Claude analysis
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_prompt}
            ]
        )
        
        analysis_text = response.content[0].text
        
        # Parse response
        score, reasoning = self._parse_claude_response(analysis_text, language)
        
        return score, reasoning, analysis_text
    
    def _format_lead_for_analysis(self, lead_data: Dict) -> str:
        """Format lead data for Claude analysis."""
        parts = [
            f"Name: {lead_data.get('name', 'Unknown')}",
            f"Email: {lead_data.get('email', 'N/A')}",
            f"Phone: {lead_data.get('phone', 'N/A')}",
            f"Property Address: {lead_data.get('property_address', 'N/A')}",
            f"Property URL: {lead_data.get('property_url', 'N/A')}",
            f"Source: {lead_data.get('source', 'Unknown')}",
        ]
        
        if lead_data.get('raw_data'):
            parts.append(f"Additional Data: {json.dumps(lead_data['raw_data'], indent=2)}")
        
        return "\n".join(parts)
    
    def _get_english_system_prompt(self) -> str:
        """System prompt for English lead qualification."""
        return """You are a real estate lead qualification specialist focused on social media leads from Latin American buyers interested in Florida properties.

IMPORTANT CONTEXT: These leads come from buyer-intent hashtags (#quieromprarencasa, #buscandocasaenmiami, #mudanzaaflorida, #comprarcasausa, etc.) and Colombian expat Facebook groups. They are REAL PEOPLE expressing genuine interest — not agents advertising. Apply a generous scoring standard appropriate for social media.

HOT: Score HOT if the person shows location interest (Miami, Florida, USA) AND any buying language (quiero comprar, busco casa, me mudo, inversión, primera casa). Contact info is NOT required — a username is enough to reach out.
Examples: mentions moving to Miami, asks about neighborhoods, asks about prices, mentions a budget, tags family/partner in property posts, asks about mortgages or financing.

WARM: Score WARM automatically if the post or caption contains ANY of these buyer-intent signals, even without location specifics:
- Hashtags like #quieromprarencasa #buscandocasa #casapropia #comprarcasa #mudanza
- Questions about buying, renting, or investing in the US
- Mentions of wanting to leave their country or relocate
- Asking for recommendations on neighborhoods or cities
- Expressing a dream of homeownership

COLD: Only score COLD if the post is clearly an agent advertisement, a brand/business promotion, spam, or has zero buying intent. Do NOT score COLD just because contact info is missing.

Evaluate the lead and respond with exactly this format:
SCORE: [HOT/WARM/COLD]
REASONING: [2-3 sentences explaining your decision]"""

    def _get_spanish_system_prompt(self) -> str:
        """System prompt for Spanish lead qualification."""
        return """Eres un especialista en calificacion de leads inmobiliarios enfocado en compradores latinoamericanos interesados en propiedades en Florida.

CONTEXTO IMPORTANTE: Estos leads vienen de hashtags con intencion de compra (#quieromprarencasa, #buscandocasaenmiami, #mudanzaaflorida, #comprarcasausa, etc.) y grupos de Facebook de colombianos en el exterior. Son PERSONAS REALES expresando interes genuino — no agentes publicitando. Aplica un criterio generoso apropiado para redes sociales.

HOT: Califica como HOT si la persona muestra interes en una ubicacion (Miami, Florida, USA) Y usa lenguaje de compra (quiero comprar, busco casa, me mudo, inversion, primera casa). No se requiere informacion de contacto — un usuario es suficiente para contactar.
Ejemplos: menciona mudarse a Miami, pregunta sobre vecindarios, pregunta sobre precios, menciona un presupuesto, etiqueta familia en posts de propiedades, pregunta sobre hipotecas o financiamiento.

WARM: Califica como WARM automaticamente si el post o caption contiene CUALQUIERA de estas senales de intencion de compra, incluso sin especificar ubicacion:
- Hashtags como #quieromprarencasa #buscandocasa #casapropia #comprarcasa #mudanza
- Preguntas sobre comprar, alquilar o invertir en EEUU
- Menciones de querer salir de su pais o reubicarse
- Pedir recomendaciones sobre vecindarios o ciudades
- Expresar el sueno de tener casa propia

COLD: Solo califica como COLD si el post es claramente publicidad de un agente, promocion de marca/negocio, spam, o no tiene ninguna intencion de compra. NO califiques como COLD solo porque falte informacion de contacto.

Evalua el lead y responde con exactamente este formato:
SCORE: [HOT/WARM/COLD]
REASONING: [2-3 oraciones explicando tu decision]"""
    
    def _parse_claude_response(self, response: str, language: str = "english") -> Tuple[str, str]:
        """Parse Claude's response to extract score and reasoning."""
        lines = response.split('\n')
        score = "WARM"  # Default
        reasoning = ""
        
        for line in lines:
            if line.startswith("SCORE:"):
                score_text = line.replace("SCORE:", "").strip().upper()
                if score_text in ["HOT", "WARM", "COLD"]:
                    score = score_text
            elif line.startswith("REASONING:"):
                reasoning = line.replace("REASONING:", "").strip()
        
        # Fallback to full response if parsing fails
        if not reasoning:
            reasoning = response[:200]
        
        return score, reasoning
    
    async def bulk_qualify_leads(self, leads: list, language: str = "english") -> list:
        """Qualify multiple leads."""
        qualified_leads = []
        for lead in leads:
            score, reasoning, analysis = self.qualify_lead(lead, language)
            qualified_leads.append({
                "lead": lead,
                "score": score,
                "reasoning": reasoning,
                "analysis": analysis
            })
        return qualified_leads
