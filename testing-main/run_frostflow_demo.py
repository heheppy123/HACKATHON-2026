from pprint import pprint

from app.db import init_db
from app.engine import FrostFlowEngine


def main() -> None:
    init_db()
    engine = FrostFlowEngine()
    risk = engine.compute_risk_map(0)
    route = engine.compute_route("SUB", "HUB", safest=True, avoid_steep=True, prefer_cleared=True)
    maintenance = engine.maintenance_plan(6)

    print("Risk map:")
    pprint({k: v.__dict__ for k, v in risk.items()})
    print("\nRoute:")
    pprint(route.__dict__)
    print("\nMaintenance:")
    pprint(maintenance)


if __name__ == "__main__":
    main()
