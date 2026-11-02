"""Koppelen met de eenmalige code uit de webapp, net als de Zonneburen Link."""

from __future__ import annotations

import asyncio

import aiohttp

from .const import FIRMWARE


class KoppelFout(Exception):
    """De reden is een sleutel uit strings.json, zodat de bewoner een leesbare melding krijgt."""

    def __init__(self, reden: str) -> None:
        super().__init__(reden)
        self.reden = reden


async def koppel(sessie: aiohttp.ClientSession, server: str, code: str, apparaat_id: str) -> dict:
    """Geeft adres, host, poort, gebruiker en wachtwoord van het eigen broker-account."""
    if not server.startswith("https://"):
        raise KoppelFout("geen_https")

    try:
        async with sessie.post(
            server.rstrip("/") + "/api/hub/koppel",
            json={"code": code.strip().upper(), "apparaatId": apparaat_id, "firmware": FIRMWARE},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as antwoord:
            if antwoord.status == 404:
                raise KoppelFout("code_onbekend")
            if antwoord.status == 403:
                raise KoppelFout("niet_toegestaan")
            if antwoord.status == 429:
                raise KoppelFout("te_vaak")
            if antwoord.status != 200:
                raise KoppelFout("niet_bereikbaar")
            gegevens = await antwoord.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as fout:
        raise KoppelFout("niet_bereikbaar") from fout

    if not all(isinstance(gegevens.get(sleutel), (str, int)) for sleutel in ("adres", "host", "poort", "gebruiker", "wachtwoord")):
        raise KoppelFout("niet_bereikbaar")
    return gegevens
