"""Costo de un boost y niveles de servidor (ADR-001 §2.6, §2.7)."""

# Puntos que cuesta cada boost. Se guarda en cada boost (points_spent): si cambia, los anteriores conservan el suyo
BOOST_COST_POINTS = 500

# Boosts vigentes que hacen falta para cada nivel
LEVEL_THRESHOLDS: dict[int, int] = {1: 5, 2: 15, 3: 30}
MAX_LEVEL = max(LEVEL_THRESHOLDS)


def level_for(active_boosts: int) -> int:
    """El nivel que corresponde a esa cantidad de boosts vigentes (0 si no llega a 5)."""
    return max((level for level, needed in LEVEL_THRESHOLDS.items() if active_boosts >= needed), default=0)
