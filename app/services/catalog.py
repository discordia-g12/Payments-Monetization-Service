"""
Catálogo de paquetes de puntos (ADR-001 §2.6). Fijo en código, con los precios que ya muestra la web
(Ajustes → Factura). El cliente manda solo el id: los puntos y el precio salen siempre de acá.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PointPackage:
    id: str
    points: int
    price_cents: int
    currency: str = "USD"


POINT_PACKAGES: dict[str, PointPackage] = {
    package.id: package
    for package in (
        PointPackage("points_500", 500, 499),
        PointPackage("points_1000", 1000, 949),
        PointPackage("points_2500", 2500, 2199),
        PointPackage("points_5000", 5000, 3999),
    )
}


def get_package(package_id: str) -> PointPackage | None:
    return POINT_PACKAGES.get(package_id)
