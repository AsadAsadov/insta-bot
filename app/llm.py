from __future__ import annotations

import os

from openai import OpenAI

SYSTEM_PROMPT = (
    "Sən Azərbaycan dilində danışan, daşınmaz əmlak üzrə peşəkar rəqəmsal "
    "köməkçisən. Məqsədin sorğuları nəzakətlə cavablamaq, alıcının ehtiyaclarını "
    "aydınlaşdırmaq və uyğun daşınmaz əmlak təklif etməkdir. Cavabların qısa, "
    "aydın və satış yönümlü olmalıdır. Ünvan, qiymət aralığı, otaq sayı və "
    "ərazi kimi məlumatları soruş. Əgər alıcı kreditlə maraqlanırsa, ilkin "
    "ödəniş və aylıq büdcəni dəqiqləşdir."
)

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def generate_reply(message_text: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message_text},
        ],
        temperature=0.4,
        max_tokens=250,
    )
    return response.choices[0].message.content.strip()
