"""DLCs conhecidos pelo conversor — apenas SUGESTÕES para a tela de configuração.

Copiado de `data/repository/DlcRepository.py` do ultrastar2singIt v1.0.2
(https://github.com/ratnapatka/ultrastar2singIt), somente as edições 2024+
(formato JSON). Atenção: quando o TitleID do DLC está nesta lista, o próprio
conversor SUBSTITUI o JSON name e o TitleID do jogo pelos valores daqui.
Confirme sempre com o `[TITLEID DO DLC]/romfs/name.txt` lido pelo DBI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DlcInfo:
    dlc_id: str
    dlc_name: str
    dlc_json_name: str
    core_id: str
    edition: str


KNOWN_DLCS: tuple[DlcInfo, ...] = (
    DlcInfo("0100AAF018059005", "Canciones Espanolas", "songs_spa", "0100AAF018058000", "2024"),
    DlcInfo("0100AAF018059003", "Deutschen Hits", "songs_ger", "0100AAF018058000", "2024"),
    DlcInfo("0100AAF018059002", "Hits Francais et Internationaux", "songs_fra", "0100AAF018058000", "2024"),
    DlcInfo("0100AAF018059001", "Hits from Australia and NZ", "songs_aus", "0100AAF018058000", "2024"),
    DlcInfo("0100AAF018059004", "International Hits", "songs_int", "0100AAF018058000", "2024"),
    DlcInfo("01001C101ED11001", "Australia and New Zealand Hits", "songs_aus", "01001C101ED10000", "2025"),
    DlcInfo("01001C101ED11002", "French Hits", "songs_fr", "01001C101ED10000", "2025"),
    DlcInfo("01001C101ED11003", "German Hits", "songs_ger", "01001C101ED10000", "2025"),
    DlcInfo("01001C101ED11004", "International Hits", "songs_int", "01001C101ED10000", "2025"),
    DlcInfo("01001C101ED11005", "Spanish Hits", "songs_spa", "01001C101ED10000", "2025"),
    DlcInfo("0100EE4020D19001", "Australia and New Zealand Hits", "songs_aus", "0100EE4020D18000", "2026"),
    DlcInfo("0100EE4020D19002", "French Hits", "songs_fr", "0100EE4020D18000", "2026"),
    DlcInfo("0100EE4020D19003", "International Hits", "songs_int", "0100EE4020D18000", "2026"),
    DlcInfo("0100EE4020D19004", "Spanish Hits", "songs_spa", "0100EE4020D18000", "2026"),
    DlcInfo("0100EE4020D19006", "UK Hits", "songs_uk", "0100EE4020D18000", "2026"),
)


def suggestions(edition: str | None = None) -> list[dict]:
    return [asdict(d) for d in KNOWN_DLCS if edition is None or d.edition == edition]


def find(dlc_id: str) -> DlcInfo | None:
    dlc_id = dlc_id.strip().upper()
    return next((d for d in KNOWN_DLCS if d.dlc_id == dlc_id), None)


def consistency_warnings(core_id: str, dlc_id: str, json_name: str) -> list[str]:
    """Avisa quando o que o usuário digitou diverge do que o conversor vai forçar."""
    info = find(dlc_id) if dlc_id else None
    if not info:
        return []
    out: list[str] = []
    if json_name and json_name != info.dlc_json_name:
        out.append(
            f"O conversor vai usar o JSON name '{info.dlc_json_name}' para o DLC {dlc_id} "
            f"(você informou '{json_name}'). Confira o name.txt."
        )
    if core_id and core_id != info.core_id:
        out.append(
            f"O conversor vai usar o TitleID do jogo '{info.core_id}' para o DLC {dlc_id} "
            f"(você informou '{core_id}')."
        )
    return out
