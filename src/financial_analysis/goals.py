"""Goal planning logic and database operations.

Manages financial goals and calculates success probabilities using Monte Carlo.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from src.financial_analysis.monte_carlo import run_monte_carlo

# Fixed RNG seed for goal-probability Monte Carlo. Not a financial figure —
# it makes an estimator reproducible so identical inputs yield an identical
# percentage. See calculate_goal_probability's docstring.
_PROBABILITY_SEED = 20260726

logger = logging.getLogger(__name__)


class GoalType(str, Enum):
    RETIREMENT = "retirement"
    MAJOR_PURCHASE = "major_purchase"
    EDUCATION = "education"
    EMERGENCY_FUND = "emergency_fund"
    OTHER = "other"


class GoalStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


@dataclass
class Goal:
    """A financial goal.

    LEGACY FIELDS (2026-07-26, owner-reported defect): `current_amount` and
    `monthly_contribution` are STATIC columns written once at goal creation
    (or by `update_goal`, which deliberately never touches them) and never
    refreshed. They are kept for backward compatibility (existing rows,
    POST payload shape) but are NOT authoritative — they drift from reality
    the moment the portfolio moves. Display, PROGRESS, and probability must
    all use the LIVE observations instead:
      current    -> src.services.north_star_glide._default_net_worth(db)
      monthly    -> src.services.north_star_glide._contribution_run_rate(db)
    See GET /analytics/goals `live` block (src/api/routes/analytics.py) and
    GET /analytics/goals/{id}/probability, which both now read the live
    functions rather than these columns. Same failure class as the W-1
    goal-target fork (src/services/goal_resolver.py) and ADR-025 §3
    (`_Schawab_USD`): two independent sources for one number.
    """
    id: int
    name: str
    target_amount: float
    target_date: date
    current_amount: float = 0.0
    monthly_contribution: float = 0.0
    goal_type: GoalType = GoalType.OTHER
    status: GoalStatus = GoalStatus.ACTIVE
    notes: Optional[str] = None
    created_at: Optional[datetime] = None

    @property
    def months_remaining(self) -> int:
        today = date.today()
        # Simple diff: (target_year - today_year) * 12 + (target_month - today_month)
        # Note: if target_date is past, returns 0
        diff = (self.target_date.year - today.year) * 12 + (self.target_date.month - today.month)
        return max(0, diff)


def ensure_goals_table(db: Any) -> None:
    """Ensure the goals table exists (for existing databases)."""
    db.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_goals_id START 1;
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_goals_id'),
            name VARCHAR(200) NOT NULL,
            target_amount DECIMAL(20,2) NOT NULL,
            target_date DATE NOT NULL,
            current_amount DECIMAL(20,2) DEFAULT 0,
            monthly_contribution DECIMAL(20,2) DEFAULT 0,
            goal_type VARCHAR(50),
            status VARCHAR(20) DEFAULT 'active',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)


def create_goal(db: Any, goal_data: Dict[str, Any]) -> Goal:
    """Create a new goal in the database."""
    target_date_str = goal_data["target_date"]
    if isinstance(target_date_str, str):
        target_date = date.fromisoformat(target_date_str)
    else:
        target_date = target_date_str

    if target_date <= date.today():
        raise ValueError("Target date must be in the future")

    sql = """
        INSERT INTO goals (
            name, target_amount, target_date, current_amount,
            monthly_contribution, goal_type, status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id, created_at
    """
    params = (
        goal_data["name"],
        float(goal_data["target_amount"]),
        target_date,
        float(goal_data.get("current_amount", 0.0)),
        float(goal_data.get("monthly_contribution", 0.0)),
        goal_data.get("goal_type", GoalType.OTHER),
        goal_data.get("status", GoalStatus.ACTIVE),
        goal_data.get("notes"),
    )
    
    row = db.execute(sql, params).fetchone()
    goal_id = row[0]
    created_at = row[1]
    
    return Goal(
        id=goal_id,
        name=goal_data["name"],
        target_amount=float(goal_data["target_amount"]),
        target_date=target_date,
        current_amount=float(goal_data.get("current_amount", 0.0)),
        monthly_contribution=float(goal_data.get("monthly_contribution", 0.0)),
        goal_type=GoalType(goal_data.get("goal_type", "other")),
        status=GoalStatus(goal_data.get("status", "active")),
        notes=goal_data.get("notes"),
        created_at=created_at,
    )


def get_goal(db: Any, goal_id: int) -> Optional[Goal]:
    """Retrieve a goal by ID."""
    sql = """
        SELECT id, name, target_amount, target_date, current_amount, 
               monthly_contribution, goal_type, status, notes, created_at
        FROM goals 
        WHERE id = ?
    """
    row = db.execute(sql, (goal_id,)).fetchone()
    if not row:
        return None
        
    return Goal(
        id=row[0],
        name=row[1],
        target_amount=float(row[2]),
        target_date=row[3],
        current_amount=float(row[4]),
        monthly_contribution=float(row[5]),
        goal_type=GoalType(row[6]) if row[6] else GoalType.OTHER,
        status=GoalStatus(row[7]) if row[7] else GoalStatus.ACTIVE,
        notes=row[8],
        created_at=row[9],
    )


_UPDATABLE_FIELDS = {"name", "target_amount", "target_date", "goal_type", "status", "notes"}


def update_goal(db: Any, goal_id: int, updates: Dict[str, Any]) -> Optional[Goal]:
    """Update editable fields of an existing goal.

    Editable: name, target_amount, target_date, goal_type, status, notes.

    `current_amount` and `monthly_contribution` are deliberately NOT
    accepted here (silently ignored if present in `updates`) — they are
    portfolio observations, derived live (see the `Goal` dataclass
    docstring), not stored user intent. Accepting them as edit inputs would
    recreate the exact two-sources-of-truth bug this function exists to fix.

    Returns the updated Goal, or None if no goal with that id exists.
    Raises ValueError if `target_date` is provided and not in the future
    (same validation as create_goal).
    """
    fields: Dict[str, Any] = {
        k: v for k, v in updates.items() if k in _UPDATABLE_FIELDS and v is not None
    }
    if not fields:
        return get_goal(db, goal_id)

    if "target_date" in fields:
        td = fields["target_date"]
        if isinstance(td, str):
            td = date.fromisoformat(td)
        if td <= date.today():
            raise ValueError("Target date must be in the future")
        fields["target_date"] = td

    if "target_amount" in fields:
        fields["target_amount"] = float(fields["target_amount"])

    set_clause = ", ".join(f"{col} = ?" for col in fields)
    params = list(fields.values()) + [goal_id]
    db.execute(
        f"UPDATE goals SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        params,
    )
    return get_goal(db, goal_id)


def delete_goal(db: Any, goal_id: int) -> bool:
    """Delete a goal by ID."""
    db.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    return True


def list_goals(db: Any) -> List[Goal]:
    """List all goals."""
    sql = """
        SELECT id, name, target_amount, target_date, current_amount, 
               monthly_contribution, goal_type, status, notes, created_at
        FROM goals 
        ORDER BY target_date ASC
    """
    rows = db.execute(sql).fetchall()
    return [
        Goal(
            id=row[0],
            name=row[1],
            target_amount=float(row[2]),
            target_date=row[3],
            current_amount=float(row[4]),
            monthly_contribution=float(row[5]),
            goal_type=GoalType(row[6]) if row[6] else GoalType.OTHER,
            status=GoalStatus(row[7]) if row[7] else GoalStatus.ACTIVE,
            notes=row[8],
            created_at=row[9],
        )
        for row in rows
    ]


def calculate_goal_probability(
    current_amount: float,
    target_amount: float,
    years: float,
    monthly_contribution: float,
    annual_return: float = 0.07,
    annual_volatility: float = 0.15,
    num_simulations: int = 1000,
    seed: Optional[int] = _PROBABILITY_SEED,
) -> float:
    """Calculate probability of reaching goal using Monte Carlo.

    Seeded by default (2026-07-26): the probability is a deterministic
    function of its inputs — the Monte Carlo is only an estimator of it —
    so an unseeded run made the Goals card's SUCCESS PROB jitter by ~1pp
    on every page load (observed 67.5% then 68.8% for identical inputs).
    A headline percentage that moves when nothing changed reads as unstable
    data. Same inputs now give the same answer; different inputs still move
    it. Pass seed=None explicitly to sample freshly.
    """
    if years <= 0:
        return 1.0 if current_amount >= target_amount else 0.0

    result = run_monte_carlo(
        initial_value=current_amount,
        annual_return=annual_return,
        annual_volatility=annual_volatility,
        years=int(max(1, round(years))),
        num_simulations=num_simulations,
        annual_contribution=monthly_contribution * 12,
        goal_target=target_amount,
        seed=seed,
    )
    
    return result.get("goal_probability", 0.0)
