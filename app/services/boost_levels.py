"""Costo de un boost, niveles de servidor y sus beneficios (ADR-001 §2.6, §2.7, §2.10)."""

from dataclasses import dataclass

# Puntos que cuesta cada boost. Se guarda en cada boost (points_spent): si cambia, los anteriores conservan el suyo
BOOST_COST_POINTS = 500

# Boosts vigentes que hacen falta para cada nivel
LEVEL_THRESHOLDS: dict[int, int] = {1: 5, 2: 15, 3: 30}
MAX_LEVEL = max(LEVEL_THRESHOLDS)


@dataclass(frozen=True)
class Benefit:
    # Estable, en inglés: los clientes y los servicios que aplican el beneficio lo usan para reconocerlo
    id: str
    name: str
    description: str


# Lo que desbloquea cada nivel (la consigna). Los de un nivel se suman a los de los anteriores. Quién aplica cada
# uno: ADR-001 §2.10
LEVEL_BENEFITS: dict[int, tuple[Benefit, ...]] = {
    1: (
        Benefit("emoji_slots_20", "+20 emojis", "20 lugares más para emojis personalizados del servidor."),
        Benefit("animated_icon", "Ícono animado", "El ícono del servidor puede ser animado."),
    ),
    2: (
        Benefit("server_banner", "Banner del servidor", "Un banner personalizado para el servidor."),
        Benefit(
            "attachments_25mb",
            "Adjuntos de 25 MB",
            "Todos los miembros suben archivos de hasta 25 MB. Con Premium personal vale el límite más alto.",
        ),
    ),
    3: (
        Benefit("vanity_url", "Invitación personalizada", "Una URL de invitación propia para el servidor."),
        Benefit("boosted_badge", "Insignia de servidor boosteado", "La insignia \"Servidor Boosteado\" para todos."),
    ),
}


def level_for(active_boosts: int) -> int:
    """El nivel que corresponde a esa cantidad de boosts vigentes (0 si no llega a 5)."""
    return max((level for level, needed in LEVEL_THRESHOLDS.items() if active_boosts >= needed), default=0)


def next_level(active_boosts: int) -> tuple[int | None, int]:
    """El próximo nivel y cuántos boosts faltan para llegar. En el máximo: (None, 0)."""
    current = level_for(active_boosts)
    if current >= MAX_LEVEL:
        return None, 0
    upcoming = current + 1
    return upcoming, LEVEL_THRESHOLDS[upcoming] - active_boosts


def unlocked_benefits(level: int) -> list[tuple[int, Benefit]]:
    """Los beneficios de ese nivel y de los anteriores, cada uno con el nivel que lo desbloquea."""
    return [(lvl, benefit) for lvl in sorted(LEVEL_BENEFITS) if lvl <= level for benefit in LEVEL_BENEFITS[lvl]]
