"""Tests for goal planning logic."""
import pytest
from datetime import date
from unittest.mock import MagicMock
from src.financial_analysis.goals import Goal, GoalType, GoalStatus


def test_goal_model():
    """Verify Goal model structure."""
    g = Goal(
        id=1,
        name="Retirement",
        target_amount=2000000.0,
        target_date=date(2040, 1, 1),
        current_amount=500000.0,
        monthly_contribution=5000.0,
        goal_type=GoalType.RETIREMENT,
        status=GoalStatus.ACTIVE,
    )
    assert g.name == "Retirement"
    assert g.months_remaining > 0


def test_calculate_goal_probability():
    """Calculate success probability using Monte Carlo."""
    from src.financial_analysis.goals import calculate_goal_probability

    from unittest.mock import patch
    
    # Mock Monte Carlo function instead of running full simulation
    with patch("src.financial_analysis.goals.run_monte_carlo") as mock_mc:
        mock_mc.return_value = {"goal_probability": 0.85}
        
        prob = calculate_goal_probability(
            current_amount=100000.0,
            target_amount=200000.0,
            years=10,
            monthly_contribution=1000.0,
            annual_return=0.07,
            annual_volatility=0.15,
        )
        
        assert prob == 0.85
        mock_mc.assert_called_once()


def test_create_goal_in_db():
    """Integration: create goal validation."""
    from src.financial_analysis.goals import create_goal
    from datetime import datetime as dt
    
    db = MagicMock()
    # Mock insert returning ID and created_at
    # The new SQL returns (id, created_at)
    db.execute.return_value.fetchone.return_value = (1, dt.now())
    
    goal_data = {
        "name": "New House",
        "target_amount": 500000.0,
        "target_date": "2030-01-01",
        "current_amount": 100000.0,
        "monthly_contribution": 2000.0,
        "goal_type": "major_purchase",
    }
    
    new_goal = create_goal(db, goal_data)
    assert new_goal.id == 1
    assert new_goal.name == "New House"
    assert new_goal.created_at is not None

def test_ensure_goals_table():
    """Verify table creation SQL is executed."""
    from src.financial_analysis.goals import ensure_goals_table
    db = MagicMock()
    ensure_goals_table(db)
    # Check that execute was called with CREATE TABLE and CREATE SEQUENCE
    assert db.execute.call_count >= 1
    call_args = db.execute.call_args[0][0]
    assert "CREATE TABLE IF NOT EXISTS goals" in call_args


def test_goal_validation_error():
    """Fail if target date is in the past."""
    from src.financial_analysis.goals import create_goal

    db = MagicMock()
    goal_data = {
        "name": "Past Goal",
        "target_amount": 100.0,
        "target_date": "2020-01-01",  # Past
        "current_amount": 0.0,
    }

    with pytest.raises(ValueError, match="Target date must be in the future"):
        create_goal(db, goal_data)
